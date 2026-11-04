# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Pushing session logs to the archive, restoring them, and deleting them there.

The engine reads a log as plain bytes (JSON lines) and knows nothing else about sessions. A
session is synced only when it was enrolled. The confirmed cursor moves only to the
``verified_to`` of a successful append; after any failure the same immutable bytes are sent
again from the cursor. Failures back off with jitter and never loop tightly. Turning sync off
bumps a generation counter that every step checks before it sends anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from trading_r1.sync.client import (
    APPLICATION,
    DEFAULT_BASE_URL,
    LIST_LIMIT,
    MAX_APPEND,
    ArchiveClient,
    ArchiveError,
    ArchiveFullError,
    DeletedError,
    DivergedError,
    NotAuthorisedError,
    OffsetMismatchError,
    RateLimitedError,
    RejectedError,
    TooLargeError,
    TransientError,
    UnreadableAnswerError,
)
from trading_r1.sync.store import DELETED, PENDING_DELETE, Entry, SyncStore, shown_state

FORMAT = "tauric.agent-session/1"
BACKOFF_BASE = 5.0  # seconds; doubles per consecutive failure
BACKOFF_CAP = 900.0
FULL_RETRY = 900.0  # an archive that is full is looked at again this often
DENIED_RETRY = 3600.0  # a key without access is tried again this often
REFUSED_KEY = "your account refused this key; check it or replace it under API keys"
RELIST_EVERY = 600.0  # the whole listing is read this often
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_LOCAL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")  # the ids the session store accepts


class RestoreError(ArchiveError):
    """A session could not be restored: the bytes are not a valid log, or it is already here."""


class _Fenced(Exception):
    """Sync was turned off while this work ran."""


class _LineTooLong(Exception):
    pass


class SyncEngine:
    def __init__(
        self,
        sessions_dir: Path,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        application: str = APPLICATION,
        store: SyncStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        labels: Callable[[str], str | None] | None = None,
        details: Callable[[str], dict[str, str]] | None = None,
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.dir = Path(sessions_dir)
        self.store = store or SyncStore()
        self.client = ArchiveClient(
            key, base_url=base_url, application=application, transport=transport
        )
        self._labels = labels
        self._details = details
        self._enabled = enabled
        self._gen = 0
        self._clock, self._rng = clock, rng
        self._locks: dict[str, asyncio.Lock] = {}
        self._listed_at = float("-inf")
        self._blocked_until = 0.0  # the archive was out of reach: passes wait until then

    async def close(self) -> None:
        await self.client.close()
        self.store.close()

    # -- switch ---------------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        """Turn sync on or off. Turning it off fences work already running: nothing more is
        sent until it is turned on again."""
        if self._enabled and not enabled:
            self._gen += 1
        self._enabled = enabled

    def _fence(self, gen: int) -> None:
        if gen != self._gen or not self._enabled:
            raise _Fenced

    # -- enrolment and state --------------------------------------------------------------
    def enrol(self, session_id: str) -> bool:
        """Start syncing a session (its whole log, from the first byte)."""
        if not _SESSION_ID.fullmatch(session_id):
            return False
        return self.store.enrol(session_id, self._clock())

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("not a session id")
        return self.dir / f"{session_id}.jsonl"

    def _size(self, session_id: str) -> int:
        try:
            return self._path(session_id).stat().st_size
        except OSError:
            return 0

    def state_of(self, session_id: str) -> str:
        """``synced``, ``pending``, ``full``, ``error`` or ``off``."""
        return shown_state(
            self.store.get(session_id), self._size(session_id), enabled=self._enabled
        )

    def status(self) -> dict[str, Any]:
        entries = self.store.entries()
        sizes = {e.session_id: self._size(e.session_id) for e in entries}
        return {
            "enabled": self._enabled,
            "counts": self.store.summary(sizes, enabled=self._enabled),
            "deleting": sum(1 for e in entries if e.tombstone == PENDING_DELETE),
        }

    async def usage(self) -> dict[str, int]:
        """``{used_bytes, allowance_bytes}`` of the account's archive."""
        return await self.client.usage()

    # -- push -----------------------------------------------------------------------------
    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def push(self, session_id: str, *, force: bool = False) -> str:
        """Send what the archive lacks of one enrolled session; returns its state. A session
        waiting out a backoff is left alone unless ``force`` is set."""
        entry = self.store.get(session_id)
        if entry is None or not self._enabled:
            return "off"
        async with self._lock(session_id):
            gen = self._gen
            entry = self.store.get(session_id)
            if entry is None:
                return "off"
            if not force and entry.retry_at > self._clock():
                return self.state_of(session_id)
            try:
                if entry.tombstone == PENDING_DELETE:
                    await self._delete_remote(session_id, gen)
                elif not entry.tombstone and (force or entry.state != "error"):
                    await self._sync(session_id, gen)
            except _Fenced:
                pass
            except ArchiveError as err:
                self._fail(session_id, err)
        return self.state_of(session_id)

    async def _sync(self, session_id: str, gen: int) -> None:
        path = self._path(session_id)
        if not path.exists():
            return
        label = self._labels(session_id) if self._labels else None
        details = self._details(session_id) if self._details else {}
        sent = _sent_mark(label, details)
        entry = self._entry(session_id)
        if entry.remote_size is None or (sent and sent != entry.label_sent):
            self._fence(gen)
            view = await self.client.put_session(session_id, label, details=details)
            fields: dict[str, Any] = {"remote_size": view["size"], "label_sent": sent}
            if view["size"] < entry.cursor:  # the archive was restored to an earlier state
                fields["cursor"] = view["size"]
            self.store.update(session_id, **fields)
        limit, strict, reduced_by, mismatches = MAX_APPEND, False, "", 0
        while True:
            cursor = self._entry(session_id).cursor
            try:
                batch = await asyncio.to_thread(self._batch, path, cursor, limit, strict)
            except _LineTooLong:
                raise RejectedError("a log line is longer than 4 MiB") from None
            if batch is None:  # the first line does not fit what the archive can take
                if reduced_by == "full":
                    return self._park(session_id, "full", "your account is full", FULL_RETRY)
                raise RejectedError("a log line is too large for your account")
            if not batch:
                break
            self._fence(gen)
            try:
                size, verified = await self.client.append(session_id, cursor, batch)
            except DivergedError:
                raise
            except OffsetMismatchError as err:
                mismatches += 1
                if err.size >= cursor or mismatches > 3:
                    raise RejectedError(
                        "your account's copy is at a different position than expected"
                    ) from None
                self.store.update(session_id, cursor=err.size, remote_size=err.size)
                continue
            except ArchiveFullError as err:
                if 0 < err.remaining < len(batch):
                    limit, strict, reduced_by = err.remaining, True, "full"
                    continue
                return self._park(session_id, "full", "your account is full", FULL_RETRY)
            except TooLargeError:
                if batch.count(b"\n") == 1:
                    raise RejectedError("a log line is too large for your account") from None
                limit, strict, reduced_by = max(1, len(batch) // 2), False, "large"
                continue
            if not cursor < verified <= cursor + len(batch):
                raise UnreadableAnswerError("your account answered with an unexpected position")
            self.store.update(
                session_id,
                cursor=verified,
                remote_size=size,
                state="active",
                error="",
                attempts=0,
                retry_at=0,
            )
            limit, strict, reduced_by = MAX_APPEND, False, ""
        self.store.update(session_id, state="active", error="", attempts=0, retry_at=0)

    @staticmethod
    def _batch(path: Path, cursor: int, limit: int, strict: bool) -> bytes | None:
        """The whole lines at ``cursor`` that fit ``limit`` (a first line alone may exceed it
        unless ``strict``, which gives None then). Empty: nothing complete to send."""
        with path.open("rb") as fh:
            fh.seek(cursor)
            data = fh.read(MAX_APPEND)
        end = data.rfind(b"\n", 0, limit) + 1
        if end:
            return data[:end]
        first = data.find(b"\n") + 1
        if first:
            return None if strict else data[:first]
        if len(data) >= MAX_APPEND:
            raise _LineTooLong
        return b""  # an unfinished last line is never sent

    def _entry(self, session_id: str) -> Entry:
        entry = self.store.get(session_id)
        if entry is None:
            raise _Fenced
        return entry

    # -- failures -------------------------------------------------------------------------
    def _park(self, session_id: str, state: str, error: str, retry_in: float | None) -> None:
        self.store.update(
            session_id,
            state=state,
            error=error,
            retry_at=0 if retry_in is None else self._clock() + retry_in,
        )

    def _backoff(self, session_id: str, error: str, hint: float | None = None) -> None:
        entry = self.store.get(session_id)
        attempts = (entry.attempts if entry else 0) + 1
        delay = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempts - 1)) * (0.5 + self._rng() / 2)
        self.store.update(
            session_id,
            attempts=attempts,
            error=error,
            retry_at=self._clock() + max(delay, hint or 0.0),
        )

    def _fail(self, session_id: str, err: ArchiveError) -> None:
        entry = self.store.get(session_id)
        deleting = entry is not None and entry.tombstone == PENDING_DELETE
        if isinstance(err, NotAuthorisedError):
            self._park(
                session_id,
                "denied",
                f"not authorised ({err.code or err.status}): {REFUSED_KEY}",
                DENIED_RETRY,
            )
        elif isinstance(err, RateLimitedError):
            self._backoff(session_id, "rate limited", err.retry_after)
        elif isinstance(err, UnreadableAnswerError):
            self._backoff(session_id, str(err))
        elif isinstance(err, TransientError) or deleting:
            self._backoff(session_id, "your account could not be reached")
        elif isinstance(err, DivergedError):
            self._park(
                session_id,
                "error",
                "this session was continued on another device, so this copy and your account's "
                f"(now {err.size} bytes) differ; both are kept as they are and nothing more is "
                "sent. Get your account's copy as a new session with: "
                f"trading-r1 sync pull {session_id} --as NEW-ID",
                None,
            )
        elif isinstance(err, DeletedError):
            self._park(session_id, "error", "the session was deleted in your account", None)
        else:
            self._park(session_id, "error", f"rejected: {err}", None)

    # -- delete ---------------------------------------------------------------------------
    async def delete(self, session_id: str) -> str:
        """Record that the owner deleted a session and tell the archive. Only an enrolled session
        is known to the archive; any other is not touched. Returns ``deleted`` once the archive
        confirmed, ``pending`` while it has not, ``off`` for a session never enrolled."""
        self.store.tombstone(session_id)
        entry = self.store.get(session_id)
        if entry is None or not entry.tombstone:
            return "off"
        if entry.tombstone == PENDING_DELETE and self._enabled:
            await self.push(session_id, force=True)
        entry = self.store.get(session_id)
        return "deleted" if entry and entry.tombstone == DELETED else "pending"

    async def _delete_remote(self, session_id: str, gen: int) -> None:
        self._fence(gen)
        await self.client.delete(session_id)
        self.store.update(
            session_id, tombstone=DELETED, state="active", error="", attempts=0, retry_at=0
        )

    # -- listing and the periodic pass ----------------------------------------------------
    async def list_remote(self) -> list[dict[str, Any]]:
        """Every session the archive holds, all pages (newest first)."""
        gen, out, after = self._gen, [], None
        while True:
            self._fence(gen)
            page, after = await self.client.list_sessions(limit=LIST_LIMIT, after=after)
            out += page
            if not after:
                return out

    async def run_once(self) -> dict[str, Any]:
        """One pass: pending deletes, tombstones of sessions that reappeared, then each session
        with something to send, oldest first. Never raises on archive trouble."""
        if not self._enabled or self._clock() < self._blocked_until:
            return self.status()
        try:
            for entry in self.store.entries():
                if entry.tombstone == PENDING_DELETE and entry.retry_at <= self._clock():
                    await self.push(entry.session_id)
            await self._reconcile()
            for entry in self.store.entries():
                if not self._enabled:
                    break
                if not self._due(entry):
                    continue
                await self.push(entry.session_id)
                after = self.store.get(entry.session_id)
                if (
                    after
                    and (after.state == "denied" or after.attempts)
                    and after.retry_at > self._clock()
                ):
                    self._blocked_until = after.retry_at  # out of reach: not every session tries
                    break
        except _Fenced:
            pass
        return self.status()

    def _due(self, entry: Entry) -> bool:
        if entry.tombstone or entry.state == "error" or entry.retry_at > self._clock():
            return False
        size = self._size(entry.session_id)
        if entry.cursor != size or (
            entry.remote_size is None and self._path(entry.session_id).exists()
        ):
            return True
        label = self._labels(entry.session_id) if self._labels else None
        return bool(label) and label != (entry.label_sent or "").split(_MARK_GAP)[0]

    async def _reconcile(self) -> None:
        """Read the whole listing now and then: a session the owner deleted that is listed again
        is deleted again, and an archive that holds less than the cursor moves the cursor back."""
        entries = self.store.entries()
        if not entries or self._clock() - self._listed_at < RELIST_EVERY:
            return
        self._listed_at = self._clock()
        try:
            listed = await self.list_remote()
        except ArchiveError:
            return
        for view in listed:
            entry = self.store.get(view["session_id"])
            if entry is None:
                continue
            if entry.tombstone:
                await self._redelete(entry.session_id)
            elif view["size"] < entry.cursor:
                self.store.update(entry.session_id, cursor=view["size"], remote_size=view["size"])

    async def _redelete(self, session_id: str) -> None:
        gen = self._gen
        async with self._lock(session_id):
            try:
                await self._delete_remote(session_id, gen)
            except ArchiveError as err:
                self.store.update(session_id, tombstone=PENDING_DELETE)
                self._fail(session_id, err)

    # -- restore --------------------------------------------------------------------------
    async def restore(self, session_id: str, as_id: str | None = None) -> Path:
        """Download a session from the archive into the log directory (not over an existing
        log), checking that it is a valid log, and enrol it with the cursor at its end. With
        ``as_id`` the copy becomes a new local session of that id instead, kept on this
        machine only (not enrolled): a way to read or export a session whose local and archived
        histories differ."""
        remote_id, session_id = session_id, as_id or session_id
        if not _SESSION_ID.fullmatch(remote_id) or (
            as_id is not None and not _LOCAL_ID.fullmatch(as_id)
        ):
            raise RestoreError("not a valid session id")
        path = self._path(session_id)
        entry = self.store.get(session_id)
        if path.exists():
            raise RestoreError(
                "a session with this id is already on this machine"
                if as_id
                else "this session is already on this machine"
            )
        if entry is not None and entry.tombstone:
            raise RestoreError("this session was deleted here")
        async with self._lock(session_id):
            meta = await self.client.metadata(remote_id)
            size = int(meta["size"])
            check = _LogCheck()
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{session_id}-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as out:
                    for start in range(0, size, MAX_APPEND):
                        end = min(start + MAX_APPEND, size)
                        chunk = await self.client.read(remote_id, start, end - 1)
                        if len(chunk) != end - start:
                            raise RestoreError("your account returned fewer bytes than it holds")
                        check.feed(chunk)
                        out.write(chunk)
                    check.finish()
                    out.flush()
                    os.fsync(out.fileno())
                if path.exists():
                    raise RestoreError("a session with this id is already on this machine")
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        if as_id:
            return path
        self.store.enrol(session_id, self._clock())
        label = meta["metadata"].get("label")
        self.store.update(session_id, cursor=size, remote_size=size, label_sent=label)
        return path


_MARK_GAP = "\n"


def _sent_mark(label: str | None, details: dict[str, str]) -> str | None:
    """What was last sent as a session's metadata, to tell when it changed: the label first."""
    if not label and not details:
        return None
    return _MARK_GAP.join([label or "", *(f"{k}={details[k]}" for k in sorted(details))])


class _LogCheck:
    """Checks downloaded bytes as format-1 lines: one JSON object per line, ``seq`` counting up
    from 0, the first line a ``session.start`` naming the format."""

    def __init__(self) -> None:
        self._tail = b""
        self._seq = 0

    def feed(self, chunk: bytes) -> None:
        *lines, self._tail = (self._tail + chunk).split(b"\n")
        for line in lines:
            self._line(line)

    def finish(self) -> None:
        if self._tail:
            raise RestoreError("the log does not end with a complete line")

    def _line(self, raw: bytes) -> None:
        try:
            record = json.loads(raw)
        except ValueError:
            raise RestoreError(f"line {self._seq} is not JSON") from None
        if not isinstance(record, dict) or record.get("seq") != self._seq:
            raise RestoreError(f"line {self._seq} breaks the sequence")
        if self._seq == 0 and (
            record.get("type") != "session.start"
            or (record.get("data") or {}).get("format") != FORMAT
        ):
            raise RestoreError("the log does not start with a session.start of the known format")
        self._seq += 1
