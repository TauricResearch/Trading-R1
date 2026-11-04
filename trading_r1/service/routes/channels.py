# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Delivery routes: the owner's channels (list, create or replace, delete, test, find a Telegram
chat) and sending or saving a session's report through them (``/sessions/{id}/send`` and
``/save``). A secret is never returned, only whether it is set and where from; a channel that
a scheduled job sends to is not removed without ``force``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading_r1.delivery import Outbox, build_channels, deliver, report_message
from trading_r1.report import snapshot_from_session
from trading_r1.service import preferences, state

router = APIRouter()


class SendRequest(BaseModel):
    """Send a session's report to the owner's configured channels."""

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(..., min_length=1, description="Channel names, e.g. email.")
    turn: int | None = Field(None, description="The turn to send (default: the last answered).")
    key: str | None = Field(
        None,
        pattern=r"^[A-Za-z0-9-]{8,64}$",
        description="Repeat a request with the same key and nothing is sent twice.",
    )
    once: bool = Field(
        False,
        description="Send this report only once to each channel; one that failed is tried again.",
    )


def _channel_row(name: str, cfg: dict[str, Any], channels: dict, problems: dict) -> dict[str, Any]:
    """One configured channel as the owner sees it: its settings, with a secret shown only as
    whether it is set and where from."""
    from trading_r1.delivery.kinds import KINDS
    from trading_r1.state import credentials

    kind = KINDS.get(str(cfg.get("kind") or name))
    settings, secrets_state = {}, {}
    for f in kind.fields if kind else ():
        if f.name not in cfg:
            continue
        if f.type == "secret":
            ref = cfg[f.name]
            if isinstance(ref, str) and credentials.valid_name(ref):
                secrets_state[f.name] = {"name": ref, **credentials.status([ref])[ref]}
        else:
            settings[f.name] = cfg[f.name]
    row: dict[str, Any] = {
        "name": name,
        "kind": kind.name if kind else cfg.get("kind"),
        "settings": settings,
        "secrets": secrets_state,
        "address": channels[name].address() if name in channels else None,
    }
    if name in problems:
        row["problem"] = problems[name]
    return row


@router.get("/channels/kinds")
async def channel_kinds() -> dict[str, Any]:
    """The kinds of channel, each with the settings it asks for (types, help, presets)."""
    from trading_r1.delivery.kinds import KINDS

    return {"kinds": [k.describe() for k in KINDS.values()]}


@router.get("/channels")
async def list_channels() -> dict[str, Any]:
    """The channels the owner configured, their settings (a secret as set or not, never its
    value), where each goes, and why any is unusable."""
    from trading_r1.delivery.config import ConfigError, read_config

    channels, problems = build_channels()
    try:
        config = read_config()
    except ConfigError:
        return {"channels": [], "problems": problems}
    names = dict.fromkeys([*config, *channels, *(n for n in problems if n != "settings")])
    return {
        "channels": [_channel_row(n, config.get(n, {}), channels, problems) for n in names],
        "problems": problems,
    }


class ChannelPut(BaseModel):
    """Create or replace one channel. ``secrets`` maps a secret field to the value to store
    (write-only: it is kept in the credentials store under the name the settings give). Its type
    is checked by hand: a framework error would quote the rejected value back."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    settings: dict[str, Any] = Field(default_factory=dict)
    secrets: Any = None


def _refusal(err: Exception) -> HTTPException:
    """A channel change that was not made, as the response: 404 for no such channel, 409 when
    scheduled jobs depend on it, else 422."""
    from trading_r1.delivery.manage import ChannelInUseError, NoSuchChannelError

    if isinstance(err, NoSuchChannelError):
        return HTTPException(status_code=404, detail=str(err))
    code = 409 if isinstance(err, ChannelInUseError) else 422
    return HTTPException(status_code=code, detail=str(err))


@router.put("/channels/{name}")
async def put_channel(name: str, req: ChannelPut) -> dict[str, Any]:
    from trading_r1.delivery.base import ChannelError
    from trading_r1.delivery.config import ConfigError
    from trading_r1.delivery.manage import save_channel
    from trading_r1.state import credentials

    new_secrets = req.secrets or {}
    if not isinstance(new_secrets, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in new_secrets.items()
    ):
        raise HTTPException(status_code=422, detail="secrets: a map of field name to text")
    try:
        entry = await asyncio.to_thread(save_channel, name, req.kind, req.settings, new_secrets)
    except (ChannelError, credentials.CredentialsError, ConfigError) as err:
        raise _refusal(err) from err
    channels, problems = build_channels()
    return _channel_row(name, entry, channels, problems)


@router.delete("/channels/{name}")
async def delete_channel(name: str, force: bool = False) -> dict[str, Any]:
    """Remove a channel. Refused while a scheduled job lists it, unless forced."""
    from trading_r1.delivery.base import ChannelError
    from trading_r1.delivery.config import ConfigError
    from trading_r1.delivery.manage import remove_channel

    try:
        users = await asyncio.to_thread(remove_channel, name, force=force)
    except (ChannelError, ConfigError) as err:
        raise _refusal(err) from err
    return {"removed": name, "jobs_affected": users}


class ChatSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_env: str
    wait_s: float = Field(60.0, ge=1, le=120)


_chat_search = asyncio.Lock()  # one search at a time: a search holds the bot's update queue


@router.post("/channels/telegram/chat")
async def find_telegram_chat(req: ChatSearch) -> dict[str, Any]:
    """Wait for a private message to the owner's bot and return who wrote. The owner confirms it
    is them before the chat is saved; the first sender is only a candidate."""
    from trading_r1.delivery.base import ChannelError
    from trading_r1.delivery.manage import refuse_model_key
    from trading_r1.delivery.telegram import TelegramError, find_private_chat
    from trading_r1.state import credentials

    token = credentials.overlay().get(req.token_env, "")
    if not credentials.valid_name(req.token_env) or not token:
        raise HTTPException(status_code=422, detail=f"{req.token_env} is not set")
    try:
        refuse_model_key("token_env", req.token_env)
    except ChannelError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if _chat_search.locked():
        raise HTTPException(status_code=409, detail="a search is already waiting")
    async with _chat_search:
        try:
            chat = await find_private_chat(token, req.wait_s)
        except TelegramError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"chat": chat}


@router.post("/channels/{name}/test")
async def test_channel(name: str) -> dict[str, Any]:
    """Send a short test message through one channel."""
    from trading_r1.delivery import Message

    channels, problems = build_channels()
    if name not in channels:
        raise HTTPException(status_code=404, detail=problems.get(name, "no such channel"))
    message = Message("Trading-R1: test", "This is a test message from Trading-R1.")
    outcomes = await deliver(Outbox(), channels, [name], message, f"test-{uuid.uuid4().hex}")
    return {"outcomes": state.outcome_rows(outcomes)}


@router.post("/sessions/{session_id}/send")
async def send_report(session_id: str, req: SendRequest) -> dict[str, Any]:
    """Send a session's report (plain text, the PDF attached) to the owner's channels."""
    snapshot = snapshot_from_session(state.SESSIONS, session_id, req.turn)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no answered turn to send")
    channels, problems = build_channels()
    message = report_message(snapshot, name=session_id, formats=preferences.report_formats())
    key = req.key or (f"report-{snapshot.created}" if req.once else uuid.uuid4().hex)
    outbox, send_key = Outbox(), f"send-{session_id}-{key}"
    if req.once:
        await asyncio.to_thread(outbox.forget_failed, [f"{send_key}:{n}" for n in req.channels])
    outcomes = await deliver(outbox, channels, req.channels, message, send_key, problems)
    return {"outcomes": state.outcome_rows(outcomes), "key": key}


class SaveRequest(BaseModel):
    """Save a session's report as files."""

    model_config = ConfigDict(extra="forbid")

    turn: int | None = Field(None, description="The turn to save (default: the last answered).")
    dir: str | None = Field(
        None, max_length=1024, description="A folder (default: the reports folder)."
    )


@router.post("/sessions/{session_id}/save")
async def save_report(session_id: str, req: SaveRequest) -> dict[str, Any]:
    """Save a session's report as files in a subdirectory of the reports folder, or of the
    folder given: the same files the folder channel writes."""
    from trading_r1.delivery.base import ChannelError
    from trading_r1.delivery.folder import FolderChannel

    snapshot = snapshot_from_session(state.SESSIONS, session_id, req.turn)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no answered turn to save")
    message = report_message(snapshot, name=session_id, formats=preferences.report_formats())
    try:
        folder = FolderChannel({"path": req.dir or ""}, {})
    except ChannelError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    receipt = await folder.send(message)
    if receipt.status != "sent" or folder.saved is None:
        raise HTTPException(status_code=422, detail=receipt.detail)
    return {"dir": str(folder.saved), "files": [a.name for a in message.attachments]}
