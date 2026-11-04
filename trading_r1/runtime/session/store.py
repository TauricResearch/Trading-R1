# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Durable sessions: one record per session, the single copy of everything that happened.

``<dir>/<id>.jsonl`` is the session record (``runtime/session/records.py``: format
``tauric.agent-session/1``), appended as things happen and never rewritten. The lines the
session's views are built from:

- ``session.start``: the first line, with the format and the client.
- ``user.message``: a turn starts, with the question (``text``) and the config it ran with
  (backbone, model, mode, version).
- ``ui.<kind>``: a step the UI showed, as the service streamed it (``data`` is the event's
  data); token and reasoning deltas are merged into one line per run of them. Replaying a
  turn's events through the client's own fold redraws it as it looked live.
- ``artifact``: an evidence record (``kind`` ``evidence``, the record as JSON), appended the
  moment the call that made it is recorded, so a turn killed mid-run keeps the evidence it had.
- ``turn.end``: how the turn ended: its ``status`` (``completed``, ``failed`` or ``cancelled``),
  the ``termination``, the messages it added to the conversation, and the rest of its
  trajectory. Written for a failed or cancelled turn too. A turn found without an end when the
  session is next written gets a ``turn.end`` of status ``interrupted``. The client never writes
  ``session.end``: a session stays open and can be continued.
- ``session.compact``: the user compacted the conversation, with the summary that stands in for
  everything before it in the model's context.
- ``model.input``, ``model.request``, ``model.response``, ``tool.call``, ``tool.result``:
  every model call and tool call (``runtime/session/recorder.py``). The views skip them.

Everything else is a view of this record. The model's context (:meth:`SessionStore.history`) is
the latest ``session.compact`` summary followed by the messages of every turn after it that
answered; a failed or cancelled turn is kept and shown but never fed back (no poison). The
session's evidence is every turn's records. The listing comes from the session index
(``runtime/session/index.py``), a SQLite view kept beside the logs and rebuilt from them.

Pure helpers for threading turns (no-poison-on-error) come first.

Reads fail open: a missing file is an empty session, and a torn last line (a crash mid-write) is
skipped. Location: ``$TR1_SESSIONS_DIR``
or ``~/.trading-r1/sessions``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from trading_r1.runtime.evidence import EvidenceRecord
from trading_r1.runtime.run_state import Termination, harness_note
from trading_r1.runtime.session import records
from trading_r1.runtime.session.index import SessionIndex
from trading_r1.runtime.session.recorder import Recording
from trading_r1.runtime.session.records import Entry
from trading_r1.state.paths import state_dir

_log = logging.getLogger(__name__)

# A session id names a file in the sessions directory: lowercase letters, digits, ``.``, ``_`` and
# ``-``, starting with a letter or digit, so no id carries a path separator, climbs out of the
# directory, or differs from another only by case.
SESSION_ID_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"
_SESSION_ID = re.compile(SESSION_ID_PATTERN)


class InvalidSessionIdError(ValueError):
    """A session id that cannot name a session log."""


_DELTAS = frozenset({"token", "reasoning"})  # streamed piecewise; merged when recorded
_NOT_RECORDED = frozenset({"done"})  # the terminal frame repeats the trajectory: turn.end has it
_KIND_CHARS = re.compile(r"[^a-z0-9_.]")
# what the views are built from
_INDEXED = (records.START, records.USER, records.TURN_END, records.COMPACT)


def default_sessions_dir() -> Path:
    """Default durable-session directory, overridable with ``TR1_SESSIONS_DIR``."""
    env = os.environ.get("TR1_SESSIONS_DIR")
    return Path(env).expanduser() if env else state_dir() / "sessions"


# ----- threading ------------------------------------------------------------- #


_UI_RECORD, _UI_TOKEN = "ui.record", "ui.token"
_UI_LAST = "ui.last_message"
_UI_IN_CONTEXT = (_UI_RECORD, _UI_TOKEN, _UI_LAST)


def strip_system(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Conversation history minus any system message (the next turn adds its own)."""
    return [m for m in (messages or []) if m.get("role") != "system"]


def is_completed(termination: str) -> bool:
    """Whether a turn finished, so that its whole messages stay in the conversation."""
    try:
        return Termination(termination).completed
    except ValueError:
        return False


def is_cut_off(termination: str) -> bool:
    """Whether a limit stopped a turn, with whatever it had written."""
    try:
        return Termination(termination).cut_off
    except ValueError:
        return False


def end_status(termination: str) -> str:
    """The ``turn.end`` status of a turn that ended as ``termination``."""
    if is_completed(termination):
        return "completed"
    return "cancelled" if termination == Termination.CANCELLED else "failed"


def _turn_number(label: Any) -> int:
    """The number in a turn label (``t3``); 0 for anything else."""
    text = str(label or "")
    return int(text[1:]) if text[:1] == "t" and text[1:].isdigit() else 0


class TurnRecorder(Recording):
    """Records one turn as it runs: call it with each event (it is an ``on_event`` sink), then
    :meth:`end` once with the outcome. It also holds the turn's model and tool recording
    (:class:`~trading_r1.runtime.session.recorder.Recording`)."""

    def __init__(
        self, store: SessionStore, session_id: str, turn: int, given: int, user_id: str = ""
    ) -> None:
        super().__init__(
            lambda entries: store._append(session_id, entries),
            lambda: store._known(session_id),
            turn=f"t{turn}",
        )
        self._store, self._id, self.number = store, session_id, turn
        self._given = given  # messages of context the turn was given; its own come after
        self._delta: tuple[str, str] | None = None  # (kind, text) awaiting a different event
        self._ended = False
        self.cause = user_id or None

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind in _NOT_RECORDED or self._ended:
            return
        if kind in _DELTAS and set(data) == {"text"}:
            if self._delta and self._delta[0] == kind:
                self._delta = (kind, self._delta[1] + data["text"])
                return
            self._flush()
            self._delta = (kind, data["text"])
            return
        self._flush()
        self._event(kind, data)

    def _flush(self) -> None:
        if self._delta:
            kind, text = self._delta
            self._delta = None
            self._event(kind, {"text": text})

    def _event(self, kind: str, data: dict[str, Any]) -> None:
        name = _KIND_CHARS.sub("_", kind.lower())
        self._store._append(self._id, [Entry(records.UI + name, data, self.turn, run=self.run)])

    def record_evidence(self, record: EvidenceRecord) -> None:
        """Append an evidence record to the log as it is made."""
        self.artifact(record.id, json.dumps(record.to_dict(), ensure_ascii=False), kind="evidence")

    def end(self, trajectory: dict[str, Any] | None, *, termination: str | None = None) -> None:
        """Close the turn: ``trajectory`` when the run returned one; otherwise (an error, a
        cancellation) the ``termination``."""
        if self._ended:
            return
        self._ended = True
        self._flush()
        traj = dict(trajectory or {})
        messages = strip_system(traj.pop("messages", None))[self._given :]
        traj.pop("evidence", None)  # already in the log, record by record
        ended = str(traj.get("termination") or termination or "error")
        self._store._append(
            self._id,
            [
                Entry(
                    records.TURN_END,
                    {
                        "status": end_status(ended),
                        "termination": ended,
                        "messages": messages,
                        "trajectory": traj,
                    },
                    self.turn,
                    run=self.run,
                ),
            ],
        )
        self._store._touch(self._id)


class SessionStore:
    """The session logs under ``directory`` (default: the user sessions dir)."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory is not None else default_sessions_dir()
        self.index = SessionIndex(self._dir)
        self._indexed = False  # the index has been brought up to date by this store
        self._inputs: dict[str, dict[str, str]] = {}  # session -> model.input digest -> line id

    @property
    def directory(self) -> Path:
        return self._dir

    def title(self, session_id: str) -> str | None:
        """A session's title (its first question, shortened), from the index; None if unknown."""
        try:
            return self.index.title_of(session_id)
        except sqlite3.Error:
            return None

    def details(self, session_id: str) -> dict[str, str]:
        """What a session's latest turn ran on, for listing: its model, profile and our version."""
        config: dict[str, Any] = {}
        for ln in self.lines(session_id, (records.USER,)):
            config = (ln.get("data") or {}).get("config") or config
        shown = {"model": "model", "profile": "profile", "client_version": "version"}
        return {k: str(config[v]) for k, v in shown.items() if config.get(v)}

    def _ensure_index(self) -> bool:
        """Bring the index up to date with the logs: every log whose size changed since it was
        indexed (all of them, for a new index) is re-indexed. False when the index can't be had
        (it is a view; the logs stay the record)."""
        try:
            indexed = self.index.sizes()
            on_disk = {p.stem for p in self._logs()}
            for sid in on_disk | set(indexed):
                if indexed.get(sid) != self._size(sid):
                    self.index.sync(sid, self._index_lines(sid), self._size(sid))
            self._indexed = True
        except sqlite3.Error as exc:
            _log.warning("session index unavailable: %s", exc)
            return False
        return True

    def _size(self, session_id: str) -> int:
        try:
            return self._log(session_id).stat().st_size
        except OSError:
            return 0

    def _logs(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(
            p
            for p in self._dir.glob("*.jsonl")
            if _SESSION_ID.fullmatch(p.stem) and not p.is_symlink()
        )

    # ----- the log ---------------------------------------------------------- #

    def _log(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise InvalidSessionIdError(f"invalid session id: {session_id[:40]!r}")
        path = self._dir / f"{session_id}.jsonl"
        if path.is_symlink():  # a link could lead out of the directory
            raise InvalidSessionIdError(f"invalid session id: {session_id[:40]!r}")
        return path

    def _append(self, session_id: str, entries: list[Entry]) -> list[str]:
        path = self._log(session_id)
        return records.append(path, entries, start=records.start_entry())

    def _known(self, session_id: str) -> dict[str, str]:
        """The ``model.input`` digests already in the session's log, with the id of their line."""
        if session_id not in self._inputs:
            self._inputs[session_id] = records.input_hashes(self._log(session_id))
        return self._inputs[session_id]

    def lines(
        self, session_id: str | None, types: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """The session's log, in order: every line but the model calls' (or the ``types``
        asked for, exact or a prefix ending in ``.``); empty when there is none. A line that
        doesn't parse (a write cut short by a crash) is skipped."""
        if not session_id:
            return []
        return list(records.read(self._log(session_id), types))

    def _index_lines(self, session_id: str) -> list[dict[str, Any]]:
        return self.lines(session_id, _INDEXED)

    # ----- writing ------------------------------------------------------------ #

    def begin(
        self,
        session_id: str,
        question: str,
        config: dict[str, Any],
    ) -> TurnRecorder:
        """Start a turn: log it, and return the recorder its events and outcome go to."""
        log = self.lines(session_id, (records.USER, records.TURN_END, records.COMPACT))
        turns, open_turn = 0, ""
        for ln in log:
            kind = ln.get("type")
            if kind == records.USER:
                turns += 1
                open_turn = str(ln.get("turn"))
            elif kind == records.TURN_END and ln.get("turn") == open_turn:
                open_turn = ""
        context = _context(log)
        entries = []
        if open_turn:  # a turn that never ended: the process died under it
            entries.append(Entry(records.TURN_END, {"status": "interrupted"}, open_turn))
        turn = turns + 1
        entries.append(Entry(records.USER, {"text": question, "config": config}, turn=f"t{turn}"))
        ids = self._append(session_id, entries)
        self._touch(session_id)
        return TurnRecorder(self, session_id, turn, len(context), ids[-1])

    def recording(self, session_id: str) -> Recording:
        """A recorder for model calls made outside a turn (compaction), which have no turn."""
        return Recording(
            lambda entries: self._append(session_id, entries), lambda: self._known(session_id)
        )

    def compact(
        self,
        session_id: str,
        summary: list[dict[str, Any]],
        stats: dict[str, Any],
        *,
        trigger: str,
    ) -> None:
        """Record a compaction, by the user (``"user"``) or before a turn (``"auto"``):
        ``summary`` stands in for the context before it."""
        data = {"messages": strip_system(summary), "stats": stats, "trigger": trigger}
        self._append(session_id, [Entry(records.COMPACT, data)])
        self._touch(session_id)

    def _touch(self, session_id: str) -> None:
        """Bring the session index up to date with the log. Best-effort: the log line is
        already written, so an index that can't be written (locked, full disk) is caught up
        on the next listing rather than failing the turn."""
        if not self._indexed:
            self._ensure_index()  # the first write also catches the index up
            return
        try:
            self.index.sync(session_id, self._index_lines(session_id), self._size(session_id))
        except sqlite3.Error as exc:  # locked, full, or rebuilt by other code: catch up later
            _log.warning("session index not updated for %s: %s", session_id, exc)
            self._indexed = False

    # ----- views ---------------------------------------------------------------- #

    def history(self, session_id: str | None) -> list[dict[str, Any]] | None:
        """The model's context for the next turn (see the module doc); ``None`` without one."""
        if not session_id:
            return None
        wanted = (records.USER, records.TURN_END, records.COMPACT, *_UI_IN_CONTEXT)
        return _context(self.lines(session_id, wanted)) or None

    def sources(self, session_id: str | None) -> list[dict[str, Any]]:
        """The conversation's sources, each once, numbered in the order they first came: a
        number lasts the whole conversation. The record each came from, where a turn ended and
        said."""
        records_of: dict[str, str] = {}
        rows: dict[str, dict[str, Any]] = {}
        for ln in self.lines(session_id, ("ui.source", records.TURN_END)):
            data = ln.get("data") or {}
            if ln.get("type") == records.TURN_END:
                for c in (data.get("trajectory") or {}).get("citations") or []:
                    if c.get("record"):
                        records_of.setdefault(c.get("url", ""), c["record"])
            elif data.get("url") and data["url"] not in rows:
                rows[data["url"]] = {
                    "index": len(rows) + 1,
                    "url": data["url"],
                    "title": data.get("title") or data["url"],
                }
        return [{**row, "record": records_of.get(url)} for url, row in rows.items()]

    def evidence(self, session_id: str | None) -> list[dict[str, Any]]:
        """Every evidence record the session's turns made, in order."""
        parts: dict[str, list[dict[str, Any]]] = {}
        for ln in self.lines(session_id, (records.ARTIFACT,)):
            data = ln.get("data") or {}
            if data.get("kind") == "evidence":
                parts.setdefault(str(data.get("artifact_id")), []).append(data)
        out = []
        for chunks in parts.values():
            text = records.parts_content(chunks)
            try:
                record = json.loads(text) if text is not None else None
            except ValueError:
                record = None
            if isinstance(record, dict):
                out.append(record)
        return out

    def turns(self, session_id: str) -> list[dict[str, Any]]:
        """The session as the user saw it, turn by turn: question, config, the events to
        replay, how it ended, with the user's compactions in place, in order."""
        out: list[dict[str, Any]] = []
        by_turn: dict[int, dict[str, Any]] = {}
        wanted = (records.USER, records.TURN_END, records.COMPACT, records.UI)
        for ln in self.lines(session_id, wanted):
            kind = str(ln.get("type"))
            data = ln.get("data") or {}
            number = _turn_number(ln.get("turn"))
            if kind == records.USER:
                entry = {
                    "type": "turn",
                    "turn": number,
                    "question": data.get("text", ""),
                    "config": data.get("config") or {},
                    "at": ln.get("time", ""),
                    "events": [],
                    "termination": None,  # no turn.end yet: running, or cut off by a crash
                }
                by_turn[number] = entry
                out.append(entry)
            elif kind.startswith(records.UI) and (t := by_turn.get(number)):
                t["events"].append({"event": kind[len(records.UI) :], "data": data})
            elif kind == records.TURN_END and (t := by_turn.get(number)):
                t["termination"] = data.get("termination")
                t["trajectory"] = data.get("trajectory") or {}
            elif kind == records.COMPACT:
                out.append(
                    {"type": "compact", "at": ln.get("time", ""), "stats": data.get("stats")}
                )
        return out

    def list(self, limit: int = 50, *, include_jobs: bool = False) -> list[dict[str, Any]]:
        """Up to ``limit`` session summaries (no bodies), most recently active first; the
        sessions of scheduled runs (``job-*``) only when asked. Empty when the index can't be
        had."""
        if not self._ensure_index():
            return []
        return self.index.sessions(limit, include_jobs=include_jobs)

    def turn_count(self, session_id: str) -> int:
        """How many turns a session has, from the index (0 when unknown)."""
        if not self._indexed and not self._ensure_index():
            return 0
        return self.index.turns(session_id)

    def exists(self, session_id: str) -> bool:
        return self._log(session_id).exists()

    def delete(self, session_id: str) -> None:
        """Remove a session: its log and its index row. A session that is not there is a no-op."""
        path = self._log(session_id)
        path.unlink(missing_ok=True)
        records.forget(path)
        self._inputs.pop(session_id, None)
        try:
            self.index.delete(session_id)
        except sqlite3.Error as exc:  # the next listing drops the row of a log that is gone
            _log.warning("session index not updated for %s: %s", session_id, exc)
            self._indexed = False


def _unfinished(turn: dict[str, Any], ended: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """What the next turn is told about a turn that did not finish: the question, how it ended,
    the records it left and what it had written. No tool scaffolding."""
    written = next(
        (
            m["content"]
            for m in reversed(messages)
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"]
        ),
        turn["written"],
    )
    note = f'An earlier turn did not finish ({ended}). The user had asked: "{turn["question"]}"'
    if turn["records"]:
        note += f" It left the records {', '.join(turn['records'])}."
    if written.strip():
        note += f"\n\nWhat it had written, unfinished:\n\n{written.strip()}"
    return harness_note(note)


def _context(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's context from a log: the latest compaction's summary, then the messages of each
    answered turn after it, and a note for a turn that did not finish."""
    messages: list[dict[str, Any]] = []
    turns: dict[Any, dict[str, Any]] = {}
    for ln in lines:
        data, kind, label = ln.get("data") or {}, ln.get("type"), ln.get("turn")
        if kind == records.COMPACT:
            messages = list(data.get("messages") or [])
            turns.clear()
        elif kind == records.USER:
            turns[label] = {"question": data.get("text", ""), "records": [], "written": ""}
        elif kind == records.TURN_END:
            ended = str(data.get("termination") or data.get("status") or "interrupted")
            turn = turns.pop(label, None)
            if is_completed(ended):
                messages += data.get("messages") or []
            elif turn:
                messages.append(_unfinished(turn, ended, data.get("messages") or []))
        elif (turn := turns.get(label)) is not None:
            if kind == _UI_RECORD and data.get("id"):
                turn["records"].append(data["id"])
            elif kind == _UI_TOKEN:
                turn["written"] += data.get("text", "")
            elif kind == _UI_LAST:
                turn["written"] = data.get("content", "")
    messages += [_unfinished(turn, "interrupted", []) for turn in turns.values()]  # a crash
    return messages
