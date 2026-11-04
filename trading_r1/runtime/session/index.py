# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The session index: the session listing, in SQLite.

The logs (``session/store.py``, format in ``session/records.py``) are the record; this
index is derived from them and can be rebuilt from them at any time, so it is never the only
copy of anything. One table:
``sessions``: id, title, created time, the time of its last turn or compaction, turn count,
and the size of the log it was indexed from.

A session is re-indexed from its log when its log changes (``sync``), and any log whose size
differs from the one indexed (a crash between append and sync, logs copied in, a new index)
is re-indexed on the next listing. A file of another schema version is replaced by a new index
(see ``state/sqlite.py``). SQLite in WAL mode lets several writers work at once. The store
treats indexing as best-effort: the log is already written when it runs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trading_r1.state.sqlite import open_store

FILENAME = "index.sqlite3"
_TITLE_MAX = 80
# what makes a session recent
_ACTIVITY = frozenset({"session.start", "user.message", "turn.end", "session.compact"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, turns INTEGER,
    log_size INTEGER
);
CREATE INDEX IF NOT EXISTS sessions_updated ON sessions (updated_at);
"""


def title(question: str) -> str:
    q = question.strip()
    return (q[:_TITLE_MAX] + ("…" if len(q) > _TITLE_MAX else "")) or "(untitled)"


class SessionIndex:
    """The index of the logs in ``directory``."""

    def __init__(self, directory: Path) -> None:
        self._path = directory / FILENAME

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A connection for one unit of work: committed when it succeeds, always closed."""
        db = open_store(self._path, _SCHEMA)
        try:
            db.row_factory = sqlite3.Row
            yield db
            db.commit()
        finally:
            db.close()

    def check(self) -> None:
        """Open the index now, so a file that cannot be used fails here and not at a listing."""
        with self._db():
            pass

    def sync(self, session_id: str, lines: list[dict[str, Any]], size: int) -> None:
        """Re-index one session from its log (``size`` bytes when read)."""
        with self._db() as db:
            _write(db, session_id, lines, size)

    def sizes(self) -> dict[str, int]:
        """Each indexed session's log size when it was indexed."""
        with self._db() as db:
            return {r["id"]: r["log_size"] for r in db.execute("SELECT id, log_size FROM sessions")}

    def sessions(self, limit: int, *, include_jobs: bool = True) -> list[dict[str, Any]]:
        """Up to ``limit`` session summaries, most recently active first; the sessions of
        scheduled runs (``job-*``) only when ``include_jobs``."""
        where = "" if include_jobs else "WHERE id NOT LIKE 'job-%' "
        with self._db() as db:
            rows = db.execute(
                "SELECT id, title, created_at, updated_at, turns FROM sessions "
                f"{where}ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows.fetchall()]

    def turns(self, session_id: str) -> int:
        """The turn count of one session (0 when it is not indexed)."""
        with self._db() as db:
            row = db.execute("SELECT turns FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return int(row["turns"]) if row else 0

    def title_of(self, session_id: str) -> str | None:
        """The title of one session (None when it is not indexed)."""
        with self._db() as db:
            row = db.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return str(row["title"]) if row else None

    def delete(self, session_id: str) -> None:
        """Forget one session."""
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def _write(db: sqlite3.Connection, session_id: str, lines: list[dict[str, Any]], size: int) -> None:
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    if not lines:
        return
    questions = [
        (ln.get("data") or {}).get("text", "") for ln in lines if ln.get("type") == "user.message"
    ]
    active = ""
    for ln in lines:
        if ln.get("type") in _ACTIVITY:
            active = ln.get("time") or active
    db.execute(
        "INSERT INTO sessions(id, title, created_at, updated_at, turns, log_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            title(questions[0]) if questions else "(untitled)",
            lines[0].get("time", ""),
            active or lines[0].get("time", ""),
            len(questions),
            size,
        ),
    )
