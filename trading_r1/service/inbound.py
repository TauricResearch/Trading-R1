# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Telegram inbound: the owner writes to their bot, the backend answers.

Only the private chat paired with the bot may ask, and only on a Telegram channel whose ``reply``
setting is ``on``; every other message is ignored without a reply. Each message is one turn of
the session ``telegram-<chat id>-<start>`` (``/new`` starts another), run unattended and sent back
as the rich text of a report. A reply to a scheduled report continues that report's session, and
the messages after it stay there until ``/new``. The turns draw on the same concurrency slots and
the same 24-hour token budget as scheduled jobs. Only the backend that owns the scheduler listens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from typing import Any

import httpx

from trading_r1.delivery import Message, Outbox, build_channels, deliver, report_message
from trading_r1.delivery.config import ConfigError, read_config
from trading_r1.delivery.telegram import TelegramChannel, TelegramError, get_updates, send_typing
from trading_r1.scheduler import Scheduler
from trading_r1.scheduler.store import now
from trading_r1.service import preferences, state
from trading_r1.service.unattended import Unattended, run_unattended
from trading_r1.state.runtime_config import runtime_config

_LOG = logging.getLogger(__name__)
TYPING_HTTP_S = 10.0
STOP_S = 8.0  # what a stop gives the turns in flight

POLL_S = 25  # the long poll; the HTTP timeout is longer
HTTP_S = 35.0
CHECK_S = 5.0  # how often the channels are read for a `reply` switched on
WAITING = 5  # messages that may wait behind the one running
MAX_CHARS = 4000
TYPING_S = 4.0
NEW_COMMANDS = ("/new", "/start")
SOURCE = "telegram"  # the run records of chat turns are filed under this
REPORT_KEY = re.compile(r"run-(\d+):")  # how the outbox files the report of a scheduled run
RECORD_GONE = "That report is too old to continue: its record is gone. Ask again on its own."


async def _pause(low: float, high: float) -> None:
    await asyncio.sleep(random.uniform(low, high))


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_REPORTED: dict[str, str] = {}  # what was last said about each unusable channel


def _report(problems: dict[str, str]) -> None:
    """Say it once when a channel becomes unusable, or its reason changes, and once when it is
    usable again."""
    for name, why in problems.items():
        if _REPORTED.get(name) != why:
            _LOG.warning("telegram %s: not listening: %s", name, why)
            _REPORTED[name] = why
    for name in set(_REPORTED) - set(problems):
        _LOG.info("telegram %s: the problem reported earlier is gone", name)
        del _REPORTED[name]


def _channels() -> dict[str, TelegramChannel]:
    """The Telegram channels that have ``reply`` on and are usable."""
    try:
        config = read_config()
    except ConfigError as err:
        _report({"settings": str(err)})
        return {}
    wanted = {
        n: c for n, c in config.items() if c.get("kind") == "telegram" and c.get("reply") == "on"
    }
    built, problems = build_channels(config=wanted)
    _report(problems)
    return {n: c for n, c in built.items() if isinstance(c, TelegramChannel)}


def _text_of(update: dict[str, Any], chat_id: int) -> str | None:
    """The text of a message from the paired private chat, else None."""
    message = update.get("message") or {}
    chat, sender = message.get("chat") or {}, message.get("from") or {}
    text = message.get("text")
    if (
        chat.get("type") == "private"
        and _int(chat.get("id")) == chat_id
        and _int(sender.get("id")) == chat_id
        and isinstance(text, str)
        and text.strip()
    ):
        return text.strip()[:MAX_CHARS]
    return None


def _replied_to(update: dict[str, Any]) -> int | None:
    """The id of the message this update replies to, if any."""
    return _int(((update.get("message") or {}).get("reply_to_message") or {}).get("message_id"))


class _Chat:
    """One paired chat: its polling, its queue of waiting messages, its one running turn."""

    def __init__(self, name: str, scheduler: Scheduler, owner: Inbound) -> None:
        self.name = name
        self.scheduler = scheduler
        self.owner = owner
        self.queue: asyncio.Queue[tuple[int, str, int | None]] = asyncio.Queue(WAITING)
        self.started = int(time.time())
        self.report_session: str | None = None  # the report's session this chat went on with

    # ----- reading -------------------------------------------------------------------- #

    async def poll(self) -> None:
        worker = asyncio.create_task(self._work())
        offset: int | None = None
        skipped = False
        warned = False
        try:
            async with httpx.AsyncClient(timeout=HTTP_S) as client:
                while (channel := _channels().get(self.name)) is not None:
                    try:
                        if not skipped:  # what arrived while the backend was down is not answered
                            last = await get_updates(client, channel.token, offset=-1, timeout=0)
                            offset = last[-1]["update_id"] + 1 if last else None
                            skipped = True
                        updates = await get_updates(
                            client,
                            channel.token,
                            offset=offset,
                            timeout=POLL_S,
                            allowed_updates=json.dumps(["message"]),
                        )
                        warned = False
                        for update in updates:
                            update_id = _int(update.get("update_id"))
                            if update_id is None or (offset is not None and update_id < offset):
                                continue
                            text = _text_of(update, channel.chat_id)
                            if text is not None:
                                await self._enqueue(channel, update_id, text, _replied_to(update))
                            offset = update_id + 1
                    except TelegramError as err:
                        if err.status in (401, 404):
                            _LOG.warning("telegram %s: %s; not listening", self.name, err)
                            self.owner.dead[self.name] = channel.token
                            return
                        if err.status == 409:
                            if not warned:
                                _LOG.warning("telegram %s: %s", self.name, err)
                                warned = True
                            await _pause(30, 60)
                        else:
                            await _pause(1, 5)
                    except httpx.HTTPError as err:
                        _LOG.warning("telegram %s: %s", self.name, type(err).__name__)
                        await _pause(1, 5)
                    except Exception:  # noqa: BLE001 (one bad update must not end the listener)
                        _LOG.exception("telegram %s: an update could not be handled", self.name)
                        await _pause(1, 5)
        finally:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker

    async def _enqueue(
        self, channel: TelegramChannel, update_id: int, text: str, replied_to: int | None
    ) -> None:
        try:
            self.queue.put_nowait((update_id, text, replied_to))
        except asyncio.QueueFull:
            await self._send(channel, update_id, Message("", "Too many messages are waiting."))

    # ----- answering ------------------------------------------------------------------ #

    async def _send(self, channel: TelegramChannel, update_id: int, message: Message) -> None:
        outcomes = await deliver(
            Outbox(), {self.name: channel}, [self.name], message, f"tg-{update_id}"
        )
        for outcome in outcomes:
            if outcome.status != "sent":
                _LOG.warning(
                    "telegram %s: the reply to update %s was %s%s",
                    self.name,
                    update_id,
                    outcome.status,
                    f" ({outcome.detail})" if outcome.detail else "",
                )

    async def _work(self) -> None:
        """Answer the waiting messages one at a time, in order."""
        while True:
            update_id, text, replied_to = await self.queue.get()
            channel = _channels().get(self.name)
            if channel is None:
                _LOG.warning(
                    "telegram %s: update %s was dropped; the channel is no longer usable",
                    self.name,
                    update_id,
                )
                continue
            try:
                answer = await self._answer(channel, update_id, text, replied_to)
                await self._send(channel, update_id, answer)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 (the next message still gets its turn)
                _LOG.exception("telegram %s: a message could not be answered", self.name)
                with contextlib.suppress(Exception):  # the channel itself may be what failed
                    note = f"This message could not be answered ({type(err).__name__})."
                    await self._send(channel, update_id, Message("", note))

    async def _answer(
        self, channel: TelegramChannel, update_id: int, text: str, replied_to: int | None
    ) -> Message:
        if text.split()[0].split("@")[0] in NEW_COMMANDS:
            self.started = max(self.started + 1, int(time.time()))
            self.report_session = None
            return Message("", "New conversation. Ask me anything.")
        if replied_to is not None and not await self._go_to_report(channel, replied_to):
            return Message("", RECORD_GONE)
        if self.report_session and not state.SESSIONS.exists(self.report_session):
            self.report_session = None  # pruned since: this chat's own conversation resumes
            return Message("", RECORD_GONE)
        return await self._turn(channel, update_id, text)

    async def _go_to_report(self, channel: TelegramChannel, message_id: int) -> bool:
        """Go on in the session of the report this message belongs to; False if that is gone. A
        message that is no report's leaves the conversation where it is."""
        key = await asyncio.to_thread(Outbox().sent_as, channel.address(), message_id)
        found = REPORT_KEY.match(key or "")
        if found is None:
            return True
        session = await asyncio.to_thread(state.jobs().run_session, int(found[1]))
        if not session or not state.SESSIONS.exists(session):
            return False
        self.report_session = session
        return True

    async def _turn(self, channel: TelegramChannel, update_id: int, text: str) -> Message:
        """Run one turn and return what to send back."""
        store = state.jobs()
        at = now()
        jobs = runtime_config().jobs
        if await asyncio.to_thread(store.tokens_last_day, at) + jobs.run_tokens > jobs.daily_tokens:
            return Message("", "The day's token ceiling is reached; try again later.")
        run_id = await asyncio.to_thread(store.claim_chat, SOURCE, reserved=jobs.run_tokens, at=at)
        session_id = self.report_session or f"telegram-{channel.chat_id}-{self.started}"
        await asyncio.to_thread(store.set_session, run_id, session_id)
        # Whatever happens next, the run row ends: it holds a token reservation until it does.
        status, termination, tokens, detail = "failed", "", None, "the turn did not finish"
        try:
            async with self.scheduler.slot():
                typing = asyncio.create_task(self._typing(channel))
                try:
                    run = await run_unattended(
                        session_id, text, profile=None, mode=preferences.current()["default_mode"]
                    )
                finally:
                    typing.cancel()
            termination, tokens, detail = run.termination, run.tokens, run.detail
            if run.snapshot is None:
                if _too_long(run):
                    return Message(
                        "", "This conversation has outgrown the model. Send /new to start another."
                    )
                return Message("", f"The answer did not complete ({run.reason}).")
            message = report_message(run.snapshot, formats=())
            status = "done"
            return message
        except asyncio.CancelledError:
            status, detail = "interrupted", "the backend was stopping"
            raise
        except Exception as err:
            detail = f"{type(err).__name__}: {err}"
            raise
        finally:
            await asyncio.to_thread(
                store.finish,
                run_id,
                status,
                now(),
                termination=termination,
                tokens=tokens,
                error=detail,
            )

    async def _typing(self, channel: TelegramChannel) -> None:
        async with httpx.AsyncClient(timeout=TYPING_HTTP_S) as client:
            while True:
                await send_typing(client, channel.token, channel.chat_id)
                await asyncio.sleep(TYPING_S)


def _too_long(run: Unattended) -> bool:
    """Whether this conversation has outgrown the model, so another message would fail too."""
    return run.termination == "context_full" or "no longer fits" in run.reason


class Inbound:
    """Starts a listener for each Telegram channel with ``reply`` on, and stops them."""

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self.dead: dict[str, str] = {}  # channel -> the token that was refused
        self._chats: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._supervise())
        self._task.add_done_callback(state.log_if_died)

    async def stop(self) -> None:
        """Stop listening and cancel the turns in flight."""
        tasks = [t for t in (self._task, *self._chats.values()) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=STOP_S)
        self._chats.clear()

    async def _supervise(self) -> None:
        while True:
            for name, task in list(self._chats.items()):
                if task.done():
                    del self._chats[name]
            for name, channel in _channels().items():
                if name not in self._chats and self.dead.get(name) != channel.token:
                    self._chats[name] = asyncio.create_task(
                        _Chat(name, self.scheduler, self).poll()
                    )
            await asyncio.sleep(CHECK_S)
