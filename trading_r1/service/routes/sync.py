# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Sync routes: the state of the session archive copy (``GET /sync``), sending now
(``POST /sync/push``), including the sessions already on this machine (``POST /sync/import``),
and getting a session back from the archive (``GET /sync/remote``, ``POST /sync/restore``).
Whether sync is on is the ``sync`` setting; the key is the Tauric backbone's.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from trading_r1.runtime.session.store import SESSION_ID_PATTERN
from trading_r1.service import preferences, state
from trading_r1.service.syncing import NO_KEY
from trading_r1.sync.client import ArchiveError, NotAuthorisedError, NotFoundError
from trading_r1.sync.engine import RestoreError, SyncEngine

router = APIRouter()


class Counts(BaseModel):
    synced: int = 0
    pending: int = 0
    full: int = 0
    error: int = 0
    off: int = 0


class Usage(BaseModel):
    used_bytes: int
    allowance_bytes: int


class SyncStatus(BaseModel):
    enabled: bool = Field(description="The sync setting.")
    state: str = Field(
        description="synced, pending, full or error; off when the setting is off; "
        f"'{NO_KEY}' without a Tauric key."
    )
    counts: Counts = Field(description="How many sessions show each state.")
    deleting: int = Field(description="Deleted sessions the archive has not yet confirmed.")
    error: str = Field("", description="The last problem, in words; it never contains the key.")
    usage: Usage | None = Field(None, description="The archive's use, when it answers.")


class PushResult(SyncStatus):
    enrolled: int = Field(0, description="Sessions newly enrolled by an import.")


class RemoteSession(BaseModel):
    id: str
    label: str | None = None
    size: int
    updated_at: str | None = None


class RemoteSessions(BaseModel):
    sessions: list[RemoteSession]


class RestoreRequest(BaseModel):
    id: str = Field(..., pattern=f"^{SESSION_ID_PATTERN}$", description="The session to restore.")
    as_: str | None = Field(
        None,
        alias="as",
        pattern=f"^{SESSION_ID_PATTERN}$",
        description="Restore it as a new session of this id, kept on this machine only (not "
        "synced), to read or export the archive's copy of a session that was continued "
        "elsewhere.",
    )

    model_config = {"populate_by_name": True}


class RestoreResult(BaseModel):
    id: str = Field(description="The local id of the restored session.")
    restored: bool


async def _status() -> SyncStatus:
    sync = state.sync()
    summary = sync.summary()
    if problem := preferences.SETTINGS.read()[1]:
        summary["error"] = f"{problem}; sync stays off until you turn it on"
    return SyncStatus(**summary, usage=await sync.usage())


def _engine() -> SyncEngine:
    """The engine, or the reason there is none (a 409)."""
    sync = state.sync()
    if not sync.on():
        raise HTTPException(status_code=409, detail="sync is off")
    engine = sync.engine()
    if engine is None:
        raise HTTPException(status_code=409, detail="no Tauric key: add one under API keys")
    return engine


@router.get("/sync", response_model=SyncStatus)
async def get_sync() -> SyncStatus:
    """Whether the sessions are copied to the archive, how many are in each state, the archive's
    use and the last problem."""
    return await _status()


@router.post("/sync/push", response_model=PushResult)
async def push() -> PushResult:
    """Send what the archive lacks now, trying again what was waiting out a failure."""
    await state.sync().push_now()
    return PushResult(**(await _status()).model_dump())


@router.post("/sync/import", response_model=PushResult)
async def import_history() -> PushResult:
    """Enrol every interactive session already on this machine and send them (scheduled runs and
    Telegram chats are never sent). Sync must be on."""
    sync = state.sync()
    if not sync.on():
        raise HTTPException(status_code=409, detail="sync is off")
    done = sync.import_history()
    await sync.push_now()
    return PushResult(**(await _status()).model_dump(), enrolled=done["enrolled"])


@router.get("/sync/remote", response_model=RemoteSessions)
async def remote() -> RemoteSessions:
    """The sessions the archive holds that are not on this machine."""
    engine = _engine()
    try:
        listed = await engine.list_remote()
    except ArchiveError as err:
        raise _unavailable(err) from err
    here = {p.stem for p in state.SESSIONS.directory.glob("*.jsonl")}
    gone = {e.session_id for e in state.sync().store.entries() if e.tombstone}
    return RemoteSessions(
        sessions=[
            RemoteSession(
                id=v["session_id"],
                label=v["metadata"].get("label"),
                size=int(v.get("size", 0)),
                updated_at=v.get("updated_at"),
            )
            for v in listed
            if v["session_id"] not in here | gone
        ]
    )


@router.post("/sync/restore", response_model=RestoreResult)
async def restore(req: RestoreRequest) -> RestoreResult:
    """Download a session from the archive (it must not exist here) and keep it in sync; with
    ``as``, as a new local session that is not synced."""
    engine = _engine()
    try:
        await engine.restore(req.id, req.as_)
    except RestoreError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except NotFoundError as err:
        raise HTTPException(status_code=404, detail="your account has no such session") from err
    except ArchiveError as err:
        raise _unavailable(err) from err
    return RestoreResult(id=req.as_ or req.id, restored=True)


def _unavailable(err: ArchiveError) -> HTTPException:
    why = "the key may not use session sync" if isinstance(err, NotAuthorisedError) else str(err)
    return HTTPException(status_code=502, detail=f"your account did not answer: {why}")
