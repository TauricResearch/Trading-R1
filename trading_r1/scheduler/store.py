# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Jobs and their runs, in `jobs.sqlite` beside the sessions (not in the rebuildable index).

A run's ``(job, occurrence)`` is unique: inserting it is claiming that fire, so two ticks or two
processes cannot both run it. The job's cursor moves in the same transaction as the claim.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_r1.state.paths import state_dir
from trading_r1.state.sqlite import open_store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL,
    cron TEXT NOT NULL DEFAULT '',
    tz TEXT NOT NULL DEFAULT 'UTC',
    run_at TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',
    deliver TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL,
    cursor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    occurrence TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    started TEXT NOT NULL,
    ended TEXT NOT NULL DEFAULT '',
    termination TEXT NOT NULL DEFAULT '',
    tokens INTEGER,
    reserved INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    delivery TEXT NOT NULL DEFAULT '[]',
    noticed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(job_id, occurrence)
);
"""


def now() -> datetime:
    return datetime.now(UTC)


def _iso(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: str
    name: str
    prompt: str
    kind: str
    cron: str
    tz: str
    run_at: str
    profile: str
    mode: str
    deliver: tuple[str, ...]
    enabled: bool
    created: str
    cursor: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prompt": self.prompt,
            "kind": self.kind,
            "cron": self.cron,
            "tz": self.tz,
            "run_at": self.run_at,
            "profile": self.profile,
            "mode": self.mode,
            "deliver": list(self.deliver),
            "enabled": self.enabled,
            "created": self.created,
        }


_JOB_COLUMNS = (
    "id, name, prompt, kind, cron, tz, run_at, profile, mode, deliver, enabled, created, cursor"
)


def _job(row: tuple) -> Job:
    *head, deliver, enabled, created, cursor = row
    return Job(*head, tuple(json.loads(deliver)), bool(enabled), created, cursor)


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or state_dir() / "jobs.sqlite"

    def _connect(self) -> sqlite3.Connection:
        return open_store(self._path, _SCHEMA, isolation_level=None, keep=("jobs", "runs"))

    def check(self) -> None:
        """Open the store now, so a file that cannot be used fails here and not at a claim."""
        self._connect().close()

    # ----- jobs ----------------------------------------------------------------------- #

    def add(self, job: dict[str, Any], at: datetime) -> Job:
        job_id = "j-" + uuid.uuid4().hex[:6]
        with self._connect() as db:
            db.execute(
                f"INSERT INTO jobs({_JOB_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    job_id,
                    job["name"],
                    job["prompt"],
                    job["kind"],
                    job.get("cron", ""),
                    job.get("tz", "UTC"),
                    job.get("run_at", ""),
                    job["profile"],
                    job.get("mode", "auto"),
                    json.dumps(list(job.get("deliver", []))),
                    _iso(at),
                    _iso(at),  # the cursor: nothing before creation ever fires
                ),
            )
        found = self.get(job_id)
        assert found is not None
        return found

    def get(self, job_id: str) -> Job | None:
        with self._connect() as db:
            row = db.execute(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list(self) -> list[Job]:
        with self._connect() as db:
            rows = db.execute(f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY created, id").fetchall()
        return [_job(r) for r in rows]

    def delete(self, job_id: str) -> bool:
        """Remove the job. Its runs stay: they are the record of what was spent, and a run id is
        never reused (a delivery is keyed by it)."""
        with self._connect() as db:
            return db.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount > 0

    def set_enabled(self, job_id: str, enabled: bool, at: datetime) -> bool:
        """Enabling a disabled job moves its cursor to now: what fell due while it was off is not
        replayed. Enabling one that is enabled changes nothing."""
        with self._connect() as db:
            if enabled:
                db.execute(
                    "UPDATE jobs SET enabled=1, cursor=? WHERE id=? AND enabled=0",
                    (_iso(at), job_id),
                )
                return db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is not None
            return db.execute("UPDATE jobs SET enabled=0 WHERE id=?", (job_id,)).rowcount > 0

    # ----- runs ----------------------------------------------------------------------- #

    def claim(
        self,
        job: Job,
        fires: list[datetime],
        *,
        status_of_latest: str,
        reserved: int,
        ceiling: int,
        at: datetime,
        error: str = "",
    ) -> tuple[int, str] | None:
        """In one transaction: record the older fires as ``missed``, the latest as
        ``status_of_latest`` (``running`` to run it, unless :meth:`_admit` refuses), and move the
        cursor to the latest. Returns the id and status of the latest's run row, or None if it was
        already claimed."""
        latest = fires[-1]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for fire in fires[:-1][-5:]:  # a long gap is summarized, not listed
                    db.execute(
                        "INSERT OR IGNORE INTO runs(job_id, occurrence, status, started, ended, "
                        "error) VALUES (?,?,'missed',?,?,'it fell due while no backend ran')",
                        (job.id, _iso(fire), _iso(at), _iso(at)),
                    )
                status = status_of_latest
                if status == "running":
                    status, error = self._admit(db, job.id, reserved, ceiling, at)
                cur = db.execute(
                    "INSERT OR IGNORE INTO runs(job_id, occurrence, status, started, ended, "
                    "reserved, error) VALUES (?,?,?,?,?,?,?)",
                    (
                        job.id,
                        _iso(latest),
                        status,
                        _iso(at),
                        "" if status == "running" else _iso(at),
                        reserved if status == "running" else 0,
                        error,
                    ),
                )
                claimed = None
                if cur.rowcount:
                    assert cur.lastrowid is not None
                    claimed = (cur.lastrowid, status)
                db.execute("UPDATE jobs SET cursor=? WHERE id=?", (_iso(latest), job.id))
                if job.kind == "once":
                    db.execute("UPDATE jobs SET enabled=0 WHERE id=?", (job.id,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return claimed

    def claim_manual(
        self, job: Job, *, reserved: int, ceiling: int, at: datetime
    ) -> tuple[int, str]:
        """Record a run the owner asked for, ``running`` unless :meth:`_admit` refuses; returns
        its id and status. The check and the insert are one transaction."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                status, error = self._admit(db, job.id, reserved, ceiling, at)
                cur = db.execute(
                    "INSERT INTO runs(job_id, occurrence, status, started, ended, reserved, "
                    "error) VALUES (?,?,?,?,?,?,?)",
                    (
                        job.id,
                        f"manual-{uuid.uuid4().hex[:8]}",
                        status,
                        _iso(at),
                        "" if status == "running" else _iso(at),
                        reserved if status == "running" else 0,
                        error,
                    ),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            assert cur.lastrowid is not None
            return cur.lastrowid, status

    def _admit(
        self, db: sqlite3.Connection, job_id: str, reserved: int, ceiling: int, at: datetime
    ) -> tuple[str, str]:
        """Whether a run may start now: ``("running", "")`` or ``("skipped", reason)``. Called
        inside the transaction that inserts the run, so two claims cannot both pass."""
        if db.execute(
            "SELECT 1 FROM runs WHERE job_id=? AND status='running'", (job_id,)
        ).fetchone():
            return "skipped", "the previous run was still going"
        if self._spent(db, at) + reserved > ceiling:
            return "skipped", "the day's token ceiling is reached"
        return "running", ""

    def claim_chat(self, source: str, *, reserved: int, at: datetime) -> int:
        """Record a run that belongs to no job (a chat turn), so the day's budget counts it."""
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO runs(job_id, occurrence, status, started, reserved) "
                "VALUES (?,?,'running',?,?)",
                (source, f"chat-{uuid.uuid4().hex[:8]}", _iso(at), reserved),
            )
            assert cur.lastrowid is not None
            return cur.lastrowid

    def tokens_last_day(self, at: datetime) -> int:
        """Tokens spent, or still reserved, by the runs started in the 24 hours before ``at``."""
        with self._connect() as db:
            return self._spent(db, at)

    @staticmethod
    def _spent(db: sqlite3.Connection, at: datetime) -> int:
        (total,) = db.execute(
            "SELECT COALESCE(SUM(COALESCE(tokens, reserved)), 0) FROM runs "
            "WHERE started > ? AND status NOT IN ('missed', 'skipped')",
            (_iso(at - timedelta(hours=24)),),
        ).fetchone()
        return int(total)

    def set_session(self, run_id: int, session_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE runs SET session_id=? WHERE id=?", (session_id, run_id))

    def run_session(self, run_id: int) -> str | None:
        """The session a run recorded, unless it has been pruned."""
        with self._connect() as db:
            found = db.execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
        return str(found[0]) if found and found[0] else None

    def old_sessions(self, job_id: str, keep: int) -> list[tuple[int, str]]:
        """The (run id, session id) of a job's runs that still name a session, beyond its
        newest ``keep`` of them."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, session_id FROM runs WHERE job_id=? AND session_id != '' "
                "ORDER BY id DESC LIMIT -1 OFFSET ?",
                (job_id, keep),
            ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def clear_session(self, run_id: int) -> None:
        """A run no longer has a session; the run itself, and its accounting, stay."""
        self.set_session(run_id, "")

    def finish(
        self,
        run_id: int,
        status: str,
        at: datetime,
        *,
        termination: str = "",
        tokens: int | None = None,
        error: str = "",
        delivery: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE runs SET status=?, ended=?, termination=?, tokens=?, error=?, delivery=? "
                "WHERE id=?",
                (
                    status,
                    _iso(at),
                    termination,
                    tokens,
                    error[:300],
                    json.dumps(delivery or []),
                    run_id,
                ),
            )

    def recover(self, at: datetime) -> int:
        """Runs left ``running`` by a backend that stopped are ``interrupted``; they are not
        retried. Their reservation stays against the day's budget."""
        with self._connect() as db:
            return db.execute(
                "UPDATE runs SET status='interrupted', ended=?, "
                "error='the backend stopped while it ran' WHERE status='running'",
                (_iso(at),),
            ).rowcount

    def unrun(self, at: datetime) -> list[tuple[int, str, str]]:
        """Runs of the last week that never produced a report and that nobody has been told
        about: ``(id, job id, error)``, oldest first."""
        with self._connect() as db:
            return db.execute(
                "SELECT id, job_id, error FROM runs WHERE noticed = 0 AND started > ? "
                "AND status IN ('missed', 'skipped', 'interrupted') "
                "AND occurrence NOT LIKE 'manual-%' ORDER BY id",
                (_iso(at - timedelta(days=7)),),
            ).fetchall()

    def mark_noticed(self, run_ids: list[int]) -> None:
        with self._connect() as db:
            db.executemany("UPDATE runs SET noticed = 1 WHERE id = ?", [(i,) for i in run_ids])

    def runs(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, occurrence, status, session_id, started, ended, termination, tokens, "
                "error, delivery FROM runs WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        names = (
            "id",
            "occurrence",
            "status",
            "session",
            "started",
            "ended",
            "termination",
            "tokens",
            "error",
            "delivery",
        )
        out = [dict(zip(names, r, strict=True)) for r in rows]
        for r in out:
            r["delivery"] = json.loads(r["delivery"])
        return out
