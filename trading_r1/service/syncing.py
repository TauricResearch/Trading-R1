# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Session sync in the service: which sessions are enrolled, the engine that sends them, and the
background task that keeps it going.

``SyncRuntime`` joins the pieces the sync package leaves open: the key and address are the
Tauric backbone's, the on/off switch is the ``sync`` preference (read each time it matters), and
only interactive sessions are enrolled (a scheduled run's log is pruned locally and a Telegram
log holds a third party's chat, so neither is ever sent). Nothing here raises into a turn or
the app: a failure is a state the person can read, never an error in what they asked for.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from trading_r1.providers import get_profile
from trading_r1.sync.client import DEFAULT_BASE_URL, ArchiveError
from trading_r1.sync.engine import SyncEngine
from trading_r1.sync.store import PENDING_DELETE, SyncStore, shown_state

_LOG = logging.getLogger(__name__)

TICK_S = 30.0
NOT_SYNCED = ("job-", "telegram-")  # sessions that never leave the machine
NO_KEY = "off: no Tauric key"
_WORST = ("error", "full", "pending")  # what the whole shows: the first of these with a session


def is_synced_kind(session_id: str) -> bool:
    """Whether a session is one the user works in (and so may be enrolled)."""
    return not session_id.startswith(NOT_SYNCED)


def tauric_access() -> tuple[str, str] | None:
    """The Tauric backbone's key and base URL, or None without a key."""
    try:
        profile = get_profile("tauric")
        key = profile.resolve_api_key()
    except Exception:  # noqa: BLE001 (a broken providers file means no key here)
        return None
    return (key, profile.base_url or DEFAULT_BASE_URL) if key else None


class SyncRuntime:
    def __init__(
        self,
        sessions_dir: Path,
        *,
        enabled: Callable[[], bool],
        access: Callable[[], tuple[str, str] | None] = tauric_access,
        labels: Callable[[str], str | None] | None = None,
        details: Callable[[str], dict[str, str]] | None = None,
        store: SyncStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        tick: float = TICK_S,
    ) -> None:
        self.dir = Path(sessions_dir)
        self.store = store or SyncStore()
        self._enabled, self._access, self._labels = enabled, access, labels
        self._details = details
        self._transport, self._tick = transport, tick
        self._engine: SyncEngine | None = None
        self._engine_for: tuple[str, str] | None = None
        self._wake = asyncio.Event()
        self._busy = asyncio.Lock()  # one pass at a time: the tick, a kick, a manual push

    # -- the engine -----------------------------------------------------------------------
    def on(self) -> bool:
        try:
            return bool(self._enabled())
        except Exception:  # noqa: BLE001 (unreadable settings: the default applies)
            return True

    def engine(self) -> SyncEngine | None:
        """The engine for the current key (rebuilt when the key or address changed), with the
        switch applied; None without a key."""
        access = self._access()
        if access is None:
            return None
        if self._engine is None or access != self._engine_for:
            if self._engine is not None:
                with contextlib.suppress(RuntimeError):
                    asyncio.ensure_future(self._engine.client.close())
            key, base = access
            self.store.clear_denied()  # a new key may be allowed where the last was not
            self._engine = SyncEngine(
                self.dir,
                key,
                base_url=base,
                store=self.store,
                transport=self._transport,
                labels=self._labels,
                details=self._details,
                enabled=self.on(),
            )
            self._engine_for = access
        self._engine.set_enabled(self.on())
        return self._engine

    def apply(self) -> None:
        """Apply the switch now (a change of the setting) and look for work."""
        with contextlib.suppress(Exception):
            self.engine()
            self.kick()

    def kick(self) -> None:
        """Ask the background task to look for work now."""
        self._wake.set()

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.client.close()
            self._engine = None
        self.store.close()

    # -- enrolment ------------------------------------------------------------------------
    def enrol(self, session_id: str) -> bool:
        """Start syncing a session, if sync is on and it is one the user works in."""
        if not is_synced_kind(session_id) or not self.on():
            return False
        return self.store.enrol(session_id, time.time())

    def import_history(self) -> dict[str, int]:
        """Enrol every interactive session already on disk."""
        enrolled = known = 0
        for path in sorted(self.dir.glob("*.jsonl")) if self.dir.is_dir() else []:
            sid = path.stem
            if not is_synced_kind(sid) or path.is_symlink():
                continue
            if self.store.get(sid) is not None:
                known += 1
            elif self.store.enrol(sid, time.time()):
                enrolled += 1
        return {"enrolled": enrolled, "already": known}

    # -- state ----------------------------------------------------------------------------
    def _size(self, session_id: str) -> int:
        try:
            return (self.dir / f"{session_id}.jsonl").stat().st_size
        except OSError:
            return 0

    def _active(self) -> bool:
        return self.on() and self._access() is not None

    def states(self, session_ids: list[str]) -> dict[str, str]:
        """What each listed session shows: synced, pending, full, error or off."""
        active = self._active()
        entries = {e.session_id: e for e in self.store.entries()}
        return {
            sid: shown_state(entries.get(sid), self._size(sid), enabled=active)
            for sid in session_ids
        }

    def summary(self) -> dict[str, Any]:
        """Whether sync is on, the state of the whole, per-state counts and the last error."""
        on, has_key = self.on(), self._access() is not None
        entries = self.store.entries()
        sizes = {e.session_id: self._size(e.session_id) for e in entries}
        counts = self.store.summary(sizes, enabled=on and has_key)
        if not on:
            state = "off"
        elif not has_key:
            state = NO_KEY
        else:
            state = next((s for s in _WORST if counts[s]), "synced")
        error = ""
        if on and has_key:
            error = next(
                (
                    e.error
                    for e in entries
                    if e.error and (e.tombstone or shown_state(e, sizes[e.session_id]) != "synced")
                ),
                "",
            )
        return {
            "enabled": on,
            "state": state,
            "counts": counts,
            "deleting": sum(1 for e in entries if e.tombstone == PENDING_DELETE),
            "error": error,
        }

    async def usage(self) -> dict[str, int] | None:
        """``{used_bytes, allowance_bytes}`` when the archive answers, else None."""
        engine = self.engine()
        if engine is None or not self.on():
            return None
        try:
            return await engine.usage()
        except ArchiveError:
            return None

    # -- work -----------------------------------------------------------------------------
    async def run_pass(self) -> None:
        """One background pass; never raises."""
        try:
            engine = self.engine()
            if engine is None or not self.on():
                return
            async with self._busy:
                await engine.run_once()
        except Exception:  # noqa: BLE001 (a sync failure never fails a turn or the app)
            _LOG.exception("session sync pass failed")

    async def push_now(self) -> None:
        """Send everything pending now, trying again what waited out a backoff."""
        engine = self.engine()
        if engine is None or not self.on():
            return
        engine._blocked_until = 0.0  # noqa: SLF001 (a person asked: try again)
        async with self._busy:
            for entry in self.store.entries():
                if not entry.tombstone:
                    await engine.push(entry.session_id, force=True)
            await engine.run_once()

    async def forget(self, session_id: str) -> str:
        """The owner deleted a session: tombstone it and tell the archive. Returns ``deleted``,
        ``pending`` (the archive has not confirmed) or ``off`` (never synced). Never raises."""
        try:
            self.store.tombstone(session_id)
            engine = self.engine()
            if engine is None or not self.on():
                entry = self.store.get(session_id)
                return "pending" if entry and entry.tombstone else "off"
            return await engine.delete(session_id)
        except Exception:  # noqa: BLE001 (the local delete has happened; the tombstone stays)
            _LOG.exception("could not tell the archive about a deleted session")
            return "pending"

    async def run(self) -> None:
        """The background task: a pass every tick and whenever kicked, until cancelled."""
        while True:
            await self.run_pass()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._tick)
            self._wake.clear()
