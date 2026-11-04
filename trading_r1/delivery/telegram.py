# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Telegram through the owner's own bot, writing to the owner's private chat."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx

from trading_r1.delivery.base import Channel, ChannelError, Field, Kind, Receipt, Status
from trading_r1.delivery.message import Message
from trading_r1.delivery.telegram_format import report_blocks
from trading_r1.state.runtime_config import runtime_config

TELEGRAM_API = "https://api.telegram.org"
PAIR_WAIT_S = 120.0  # how long pairing waits for the first message
PAIR_POLL_S = 35.0  # outlasts the long poll below
PAIR_PAUSE_S = 1.0
TELEGRAM_LIMIT = 4000  # the API takes 4096 characters; leave room


class _HideBotToken(logging.Filter):
    """Telegram puts the bot token in the request URL; the HTTP client logs URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"/bot[^/\s]+/", "/bot***/", record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_HideBotToken())


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Pieces of at most ``limit`` characters, cut at a blank line, else a line, else a space."""
    pieces: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = max(rest.rfind(sep, 0, limit) for sep in ("\n\n", "\n", " "))
        cut = cut if cut > limit // 2 else limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return [*pieces, rest] if rest else pieces


def pack_blocks(blocks: tuple[str, ...], limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Blocks joined into messages of at most ``limit`` characters, never cut inside a block (a
    cut would leave a tag open). A block longer than the limit gives ``[]``: send plain text."""
    pieces: list[str] = []
    for block in blocks:
        if len(block) > limit:
            return []
        if pieces and len(pieces[-1]) + 2 + len(block) <= limit:
            pieces[-1] += "\n\n" + block
        else:
            pieces.append(block)
    return pieces


class TelegramChannel(Channel):
    """The owner's own bot, writing to the owner's private chat: plain text (no formatting, no
    link previews) in pieces, then the PDF as a document."""

    name = "telegram"

    def __init__(
        self,
        cfg: Mapping[str, Any],
        env: Mapping[str, str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        env_name = str(cfg.get("token_env") or "")
        self.token = env.get(env_name, "") if env_name else ""
        self.chat_id: int = cfg["chat_id"]
        if not self.token:
            raise ChannelError(f"telegram: set {env_name or 'token_env'} (the bot token)")
        self.base = TELEGRAM_API
        self._transport = transport

    def address(self) -> str:
        return f"telegram chat {self.chat_id}"

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***")

    async def _post(self, client: httpx.AsyncClient, method: str, **kw: Any) -> Receipt:
        try:
            resp = await client.post(f"{self.base}/bot{self.token}/{method}", **kw)
        except httpx.TransportError as err:
            return Receipt("unknown", self._redact(f"{type(err).__name__}: {err}")[:300])
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code == 200 and body.get("ok") is True:
            result = body.get("result")
            sent_as = result.get("message_id") if isinstance(result, dict) else None
            return Receipt("sent", ids=(sent_as,) if isinstance(sent_as, int) else ())
        detail = self._redact(f"HTTP {resp.status_code}: {body.get('description', '')}")[:300]
        # a 4xx answer is a definite no; anything else may have gone through
        return Receipt("failed" if 400 <= resp.status_code < 500 else "unknown", detail)

    async def _text(
        self, client: httpx.AsyncClient, pieces: list[str], rich: bool
    ) -> tuple[Receipt, int, tuple[int, ...]]:
        """Send the pieces in order; the receipt of the first that did not go, how many did, and
        their ids."""
        ids: tuple[int, ...] = ()
        for done, piece in enumerate(pieces):
            body: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": piece,
                "disable_web_page_preview": True,
            }
            if rich:
                body["parse_mode"] = "HTML"
            receipt = await self._post(client, "sendMessage", json=body)
            if receipt.status != "sent":
                return receipt, done, ids
            ids += receipt.ids
        return Receipt("sent"), len(pieces), ids

    async def send(self, message: Message) -> Receipt:
        plain = split_text(f"{message.subject}\n\n{message.text}")
        formatted = pack_blocks(report_blocks(message))
        parts = formatted or plain
        async with httpx.AsyncClient(
            timeout=runtime_config().delivery.send_timeout_s, transport=self._transport
        ) as client:
            receipt, done, ids = await self._text(client, parts, rich=bool(formatted))
            if receipt.status == "failed" and formatted and done == 0:
                parts = plain  # Telegram would not parse the markup: say it plainly
                receipt, done, ids = await self._text(client, parts, rich=False)
            total = len(parts) + len(message.sendable())
            if receipt.status != "sent":
                return self._partial(receipt, done, total, ids)
            for a in message.sendable():
                receipt = await self._post(
                    client,
                    "sendDocument",
                    data={"chat_id": str(self.chat_id)},
                    files={"document": (a.name, a.data, a.content_type)},
                )
                if receipt.status != "sent":
                    return self._partial(receipt, done, total, ids)
                done += 1
                ids += receipt.ids
        return Receipt("sent", f"{done} of {total} parts", ids)

    @staticmethod
    def _partial(receipt: Receipt, done: int, total: int, ids: tuple[int, ...]) -> Receipt:
        """A failure after some parts went out leaves a message half delivered: not `failed`."""
        status: Status = receipt.status if done == 0 else "unknown"
        return Receipt(status, f"{receipt.detail} ({done} of {total} parts delivered)", ids)


class TelegramError(Exception):
    """The bot cannot be read: a wrong token, or another consumer holds its updates."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _bot_error(resp: httpx.Response) -> TelegramError:
    try:
        detail = str(resp.json().get("description") or "")
    except ValueError:
        detail = ""
    status = resp.status_code
    if status in (401, 404):
        return TelegramError(
            "Telegram rejected the bot token (copy it again from @BotFather)", status
        )
    if status == 409:
        return TelegramError(
            f"the bot has a webhook or another reader; remove it, then retry. {detail}".strip(),
            status,
        )
    return TelegramError(f"Telegram answered {status}: {detail}".strip(), status)


async def get_updates(
    client: httpx.AsyncClient, token: str, *, offset: int | None = None, **params: Any
) -> list[dict[str, Any]]:
    """One ``getUpdates`` call (long polling when ``timeout`` is given). The client's timeout
    must exceed the poll's. Raises ``TelegramError`` for a refused call and lets a network
    error (``httpx.HTTPError``) through."""
    if offset is not None:
        params["offset"] = offset
    resp = await client.get(f"{TELEGRAM_API}/bot{token}/getUpdates", params=params)
    if resp.status_code != 200:
        raise _bot_error(resp)
    return list(resp.json().get("result", []))


async def send_typing(client: httpx.AsyncClient, token: str, chat_id: int) -> None:
    """Show "typing..." in the chat for a few seconds; a failure is not worth reporting."""
    with contextlib.suppress(httpx.HTTPError):
        await client.post(
            f"{TELEGRAM_API}/bot{token}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )


async def find_private_chat(
    token: str, wait_s: float = PAIR_WAIT_S, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """Wait for a message sent to the bot in a private chat and return that chat (its id, first
    name and username). Whoever wrote is only a candidate: the owner confirms it is them.
    Raises ``TelegramError`` when the token is rejected or the bot's updates are held elsewhere."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    offset: int | None = None
    async with httpx.AsyncClient(timeout=PAIR_POLL_S, transport=transport) as client:
        while loop.time() < deadline:
            try:
                updates = await get_updates(
                    client,
                    token,
                    offset=offset,
                    timeout=min(20, max(1, int(deadline - loop.time()))),
                )
            except httpx.HTTPError:
                await asyncio.sleep(PAIR_PAUSE_S)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                chat = (u.get("message") or {}).get("chat") or {}
                if chat.get("type") == "private":
                    return {k: chat.get(k) for k in ("id", "first_name", "username")}
    return None


KIND = Kind(
    name="telegram",
    label="Telegram",
    summary="Reports to your own Telegram chat through a bot you create with @BotFather.",
    fields=(
        Field(
            "token_env",
            "Bot token",
            "secret",
            required=True,
            default="TR1_TELEGRAM_BOT_TOKEN",
            help="From @BotFather.",
        ),
        Field(
            "chat_id",
            "Chat",
            "int",
            required=True,
            help="Found by writing to your bot once; you confirm it is you.",
        ),
        Field(
            "reply",
            "Reply",
            "choice",
            default="off",
            choices=("off", "on"),
            help="on: answer the messages you send to the bot, while the backend runs.",
        ),
    ),
    make=TelegramChannel,
)
