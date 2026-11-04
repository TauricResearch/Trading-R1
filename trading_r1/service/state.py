# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Process-wide state and the helpers every router shares: the stores (sessions, memory, user
settings), the per-session turn locks, the handle of the scheduler this backend owns
(``SCHEDULER``, set by the app's lifespan), and the lookups built on them (the preferences in
effect, the runtime config a run starts with, backbone resolution, the job store, delivery
outcomes). Routers reach these as ``state.NAME`` at call time, so one assignment here, or a
test's patch of it, is seen everywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import weakref

from fastapi import HTTPException

from trading_r1 import __version__
from trading_r1.assembly import default_skill_registry, prompt_overhead
from trading_r1.delivery import Outcome
from trading_r1.providers import ModelProfile, ProfileSelectionError, get_profile
from trading_r1.runtime.memory import MemoryStore
from trading_r1.runtime.session.store import SessionStore, default_sessions_dir
from trading_r1.scheduler import JobStore, Scheduler
from trading_r1.service import preferences
from trading_r1.service.syncing import SyncRuntime

_LOG = logging.getLogger(__name__)

API_VERSION = __version__  # product version, surfaced via /version, /health, and the TUI footer

SESSIONS = SessionStore(default_sessions_dir())
# One lock per session, held while a turn runs or the session is compacted: either starts from
# the history and evidence the previous one left. Weak, so a session's lock goes when nothing
# holds or awaits it.
SESSION_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def session_lock(session_id: str) -> asyncio.Lock:
    lock = SESSION_LOCKS.get(session_id)
    if lock is None:
        lock = SESSION_LOCKS[session_id] = asyncio.Lock()
    return lock


MEMORY = MemoryStore()  # the user's notes: read on session turns, written only on attended ones

SCHEDULER: Scheduler | None = None  # set while this backend owns the scheduler
SYNC: SyncRuntime | None = None  # the session sync, made on first use


def resolve_profile(name: str | None) -> ModelProfile:
    """Resolve a backbone for an endpoint that *acts* on one. With no name it uses the
    auto-default (:func:`get_profile`); an unknown/typo'd name or explicit selection is a
    400 (``ProfileSelectionError``)."""
    try:
        return get_profile(name)
    except ProfileSelectionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def sync() -> SyncRuntime:
    """The session sync of this process: the enrolments, the engine, the switch."""
    global SYNC
    if SYNC is None:
        SYNC = SyncRuntime(
            SESSIONS.directory,
            enabled=lambda: bool(preferences.current()["sync"]),
            labels=SESSIONS.title,
            details=SESSIONS.details,
        )
    return SYNC


def sync_kick() -> None:
    """Ask the sync to look for work (a turn ended). Best-effort, never raises."""
    with contextlib.suppress(Exception):  # a turn's end is not the place for a sync failure
        sync().kick()


def prompt_overhead_of(session_id: str | None, mode: str | None = None) -> int:
    """What the next request of a session carries beside its history."""
    return prompt_overhead(
        SESSIONS.history(session_id), skills=default_skill_registry(), mode=mode, memory=MEMORY
    )


def jobs() -> JobStore:
    return JobStore()


def outcome_rows(outcomes: list[Outcome]) -> list[dict[str, str]]:
    """Delivery outcomes as the API and the run record show them."""
    return [
        {"channel": o.channel, "address": o.address, "status": o.status}
        | ({"detail": o.detail} if o.detail else {})
        | ({"repeated": True} if o.repeated else {})
        for o in outcomes
    ]


def log_if_died(task: asyncio.Task[None]) -> None:
    """A background task that ends with an error says so now, not at shutdown."""
    if not task.cancelled() and task.exception() is not None:
        _LOG.error("background task ended: %r", task.exception())
