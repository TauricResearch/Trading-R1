# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The sync bookkeeping, in ``sync.sqlite`` beside the other stores.

One row per enrolled session: how many bytes of its log the archive has confirmed (``cursor``),
what went wrong last, when to try again, and whether the owner deleted it (a tombstone, kept so
a deleted session that reappears in the archive can be deleted again). A session that is not
enrolled has no row and never leaves the machine.

``state`` is what the last attempt left: ``active`` (nothing wrong), ``full`` (the allowance is
used up), ``denied`` (the key may not use the archive) or ``error`` (this session stopped).
What a session shows (:func:`shown_state`) also depends on its log's size.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from trading_r1.state.paths import private_state_dir
from trading_r1.state.sqlite import open_store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_sessions (
    session_id TEXT PRIMARY KEY,
    cursor INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active',
    error TEXT NOT NULL DEFAULT '',
    remote_size INTEGER,
    label_sent TEXT,
    tombstone INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    enrolled_at REAL NOT NULL
)
"""

PENDING_DELETE = 1  # tombstone: the archive has not yet confirmed the delete
DELETED = 2  # tombstone: the archive confirmed it


@dataclass(frozen=True)
class Entry:
    session_id: str
    cursor: int
    state: str
    error: str
    remote_size: int | None  # None until the archive has the session
    label_sent: str | None
    tombstone: int
    retry_at: float
    attempts: int
    enrolled_at: float


_COLUMNS = ", ".join(f.name for f in fields(Entry))
_SETTABLE = {f.name for f in fields(Entry)} - {"session_id", "enrolled_at"}


def shown_state(entry: Entry | None, local_size: int, *, enabled: bool = True) -> str:
    """``synced`` (the cursor is the log's size), ``pending``, ``full``, ``error`` or ``off``
    (not enrolled, deleted, or sync is off)."""
    if entry is None or entry.tombstone or not enabled:
        return "off"
    if entry.state == "error":
        return "error"
    if entry.cursor == local_size:
        return "synced"
    return {"full": "full", "denied": "error"}.get(entry.state, "pending")


class SyncStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or private_state_dir() / "sync.sqlite"
        self._db = open_store(self.path, _SCHEMA, keep=("sync_sessions",))

    def close(self) -> None:
        self._db.close()

    def _row(self, row: sqlite3.Row | tuple | None) -> Entry | None:
        return Entry(*row) if row else None

    def get(self, session_id: str) -> Entry | None:
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM sync_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return self._row(row)

    def entries(self) -> list[Entry]:
        """Every row, in enrolment order."""
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM sync_sessions ORDER BY enrolled_at, rowid")
        return [Entry(*r) for r in rows.fetchall()]

    def enrol(self, session_id: str, now: float) -> bool:
        """Start syncing a session; False if it was already enrolled (or deleted)."""
        with self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO sync_sessions (session_id, enrolled_at) VALUES (?, ?)",
                (session_id, now),
            )
        return cur.rowcount == 1

    def update(self, session_id: str, **values: Any) -> None:
        unknown = set(values) - _SETTABLE
        if unknown:
            raise ValueError(f"unknown columns: {sorted(unknown)}")
        if not values:
            return
        sets = ", ".join(f"{name} = ?" for name in values)
        with self._db:
            self._db.execute(
                f"UPDATE sync_sessions SET {sets} WHERE session_id = ?",
                (*values.values(), session_id),
            )

    def tombstone(self, session_id: str) -> bool:
        """Record that the owner deleted an enrolled session. False if it was never enrolled."""
        with self._db:
            cur = self._db.execute(
                "UPDATE sync_sessions SET tombstone = ?, state = 'active', error = '', "
                "retry_at = 0, attempts = 0 WHERE session_id = ? AND tombstone = 0",
                (PENDING_DELETE, session_id),
            )
        return cur.rowcount == 1

    def clear_denied(self) -> None:
        """Forget that the key was refused, so the next pass tries again (the key changed)."""
        with self._db:
            self._db.execute(
                "UPDATE sync_sessions SET state = 'active', error = '', retry_at = 0, "
                "attempts = 0 WHERE state = 'denied'"
            )

    def summary(self, sizes: dict[str, int], *, enabled: bool = True) -> dict[str, int]:
        """How many sessions show each state, given the log sizes (a missing log counts as 0)."""
        counts = {"synced": 0, "pending": 0, "full": 0, "error": 0, "off": 0}
        for entry in self.entries():
            if entry.tombstone:
                continue
            counts[shown_state(entry, sizes.get(entry.session_id, 0), enabled=enabled)] += 1
        return counts
