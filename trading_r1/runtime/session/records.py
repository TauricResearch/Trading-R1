# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The session record: the file format and how it is written and read.

A session is one JSON-lines file, ``tauric.agent-session/1``. Each line is an envelope::

    {"seq": 0, "id": "e0", "type": "session.start", "time": "…Z", "turn": "t1", "call": …,
     "parent": …, "run": …, "data": {…}}

``seq`` counts lines from 0 with no gap, ``id`` is ``e<seq>``, ``turn`` is ``t<n>``; ``call``,
``parent`` (the event that caused this one) and ``run`` appear where they apply. Line 0 is
``session.start`` and names the format and the client. A line is whole or absent: a write cut
short by a crash is cut away before the next append, so the file is always the lines that were
fully written.

The writer takes an exclusive file lock (:func:`fcntl.flock`) and a process-wide lock for each
append, and derives the next ``seq`` from the file's tail, so threads, tasks and processes can
append to one session at once and the numbering holds. A line over :data:`LINE_MAX` bytes has
its ``data`` stored as ``artifact`` lines (parts) just before it and replaced by a reference,
which readers resolve.

Readers skip the lines they do not need without parsing them: the type is read from the start
of the line.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "tauric.agent-session/1"
LINE_MAX = 3 * 1024 * 1024  # bytes; a longer line is stored as artifact parts
PART_CHARS = 256 * 1024  # characters of content in one artifact part
_BLOCK = 64 * 1024

# Types of the lines the views are built from (the others are stored for the archive).
START, USER, TURN_END, COMPACT = (
    "session.start",
    "user.message",
    "turn.end",
    "session.compact",
)
MODEL_REQUEST, MODEL_RESPONSE, MODEL_INPUT = "model.request", "model.response", "model.input"
TOOL_CALL, TOOL_RESULT, ARTIFACT = "tool.call", "tool.result", "artifact"
UI = "ui."  # prefix: a step the UI showed

REQUIRED_TYPES = (
    START,
    USER,
    MODEL_REQUEST,
    MODEL_RESPONSE,
    TOOL_CALL,
    TOOL_RESULT,
    ARTIFACT,
    TURN_END,
)


def now() -> str:
    """The current time as RFC 3339 UTC with ``Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Entry:
    """A line to append; ``seq``, ``id`` and (unless given) ``time`` are assigned by the writer."""

    type: str
    data: Mapping[str, Any] | None = None
    turn: str | None = None
    call: str | None = None
    parent: str | None = None
    run: str | None = None
    time: str | None = None


def client_info() -> dict[str, str]:
    from trading_r1 import __version__

    return {"name": "trading-r1", "version": __version__}


def start_entry(**extra: Any) -> Entry:
    """The ``session.start`` line of a new session."""
    return Entry(START, {"format": FORMAT, "client": client_info(), **extra})


def encode(envelope: Mapping[str, Any]) -> bytes:
    """One line: compact JSON in the key order readers rely on, ending in a newline."""
    return (
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    ).encode("utf-8")


def _envelope(entry: Entry, seq: int) -> dict[str, Any]:
    env: dict[str, Any] = {
        "seq": seq,
        "id": f"e{seq}",
        "type": entry.type,
        "time": entry.time or now(),
    }
    for key, value in (
        ("turn", entry.turn),
        ("call", entry.call),
        ("parent", entry.parent),
        ("run", entry.run),
    ):
        if value is not None:
            env[key] = value
    if entry.data is not None:
        env["data"] = entry.data
    return env


def artifact_entries(
    artifact_id: str,
    content: str,
    *,
    media_type: str = "application/json",
    kind: str,
    turn: str | None = None,
    call: str | None = None,
    parent: str | None = None,
    run: str | None = None,
) -> list[Entry]:
    """``content`` as ``artifact`` lines, split into parts that each fit a line. ``kind`` says
    what it holds (``evidence``, ``result``, ``spill``)."""
    chunks = [content[i : i + PART_CHARS] for i in range(0, len(content), PART_CHARS)] or [""]
    return [
        Entry(
            ARTIFACT,
            {
                "artifact_id": artifact_id,
                "kind": kind,
                "media_type": media_type,
                "part": n,
                "parts": len(chunks),
                "content": chunk,
            },
            turn=turn,
            call=call,
            parent=parent,
            run=run,
        )
        for n, chunk in enumerate(chunks)
    ]


# ----- writing ---------------------------------------------------------------- #

_lock = threading.Lock()
_tails: dict[str, tuple[int, int, int]] = {}  # path -> (inode, size, next seq) as last written


def forget(path: Path) -> None:
    """Drop what is remembered about a log (it was deleted or replaced)."""
    with _lock:
        _tails.pop(str(path), None)


def _read_at(fd: int, start: int, end: int) -> bytes:
    out = []
    while start < end:
        chunk = os.pread(fd, end - start, start)
        if not chunk:
            break
        out.append(chunk)
        start += len(chunk)
    return b"".join(out)


def _line_end(fd: int, size: int) -> int:
    """The offset just past the last newline (0 when there is none)."""
    end = size
    while end > 0:
        start = max(0, end - _BLOCK)
        block = _read_at(fd, start, end)
        at = block.rfind(b"\n")
        if at >= 0:
            return start + at + 1
        end = start
    return 0


def _last_line(fd: int, end: int) -> bytes:
    """The last complete line of the first ``end`` bytes (without its newline)."""
    cur = end - 1  # the line's content ends before the final newline
    chunks: list[bytes] = []
    while cur > 0:
        start = max(0, cur - _BLOCK)
        block = _read_at(fd, start, cur)
        at = block.rfind(b"\n")
        if at >= 0:
            chunks.append(block[at + 1 :])
            break
        chunks.append(block)
        cur = start
    return b"".join(reversed(chunks))


def _count_lines(fd: int, size: int) -> int:
    return sum(_read_at(fd, s, min(s + _BLOCK, size)).count(b"\n") for s in range(0, size, _BLOCK))


def _next_seq(fd: int, path: str) -> int:
    """The seq of the next line, from the file's tail. An unterminated tail (a write cut short)
    is cut away first."""
    st = os.fstat(fd)
    cached = _tails.get(path)
    if cached and cached[:2] == (st.st_ino, st.st_size):
        return cached[2]
    size = st.st_size
    if size == 0:
        return 0
    end = _line_end(fd, size)
    if end < size:
        os.ftruncate(fd, end)
    if end == 0:
        return 0
    try:
        last = json.loads(_last_line(fd, end))
        return int(last["seq"]) + 1
    except (ValueError, KeyError, TypeError):
        return _count_lines(fd, end)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def append(path: Path, entries: Sequence[Entry], *, start: Entry | None = None) -> list[str]:
    """Append ``entries`` to the log at ``path`` as one write; return their ids. ``start`` is the
    ``session.start`` line written first when the log is empty. A line over :data:`LINE_MAX` is
    stored as artifact parts and a reference."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path)
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            seq = _next_seq(fd, key)
            first = [start, *entries] if start is not None and seq == 0 else list(entries)
            queue = [(e, e is not start) for e in first]  # (entry, one of the given entries)
            chunks: list[bytes] = []
            ids: list[str] = []  # one per entry given: the id of its line
            while queue:
                entry, given = queue.pop(0)
                line = encode(_envelope(entry, seq))
                if len(line) > LINE_MAX and entry.type != ARTIFACT:
                    spill_id = f"spill-e{seq}"
                    parts = artifact_entries(
                        spill_id,
                        json.dumps(entry.data, ensure_ascii=False, default=str),
                        kind="spill",
                        turn=entry.turn,
                        call=entry.call,
                        parent=entry.parent,
                        run=entry.run,
                    )
                    stamp = entry.time or now()
                    stub = replace(entry, data={"artifact": spill_id, "spilled": True}, time=stamp)
                    queue[0:0] = [(replace(p, time=stamp), False) for p in parts]
                    queue.insert(len(parts), (stub, given))
                    continue
                chunks.append(line)
                if given:
                    ids.append(f"e{seq}")
                seq += 1
            payload = b"".join(chunks)
            _write_all(fd, payload)
            st = os.fstat(fd)
            _tails[key] = (st.st_ino, st.st_size, seq)
        finally:
            os.close(fd)
    return ids


# ----- reading ---------------------------------------------------------------- #

_TYPE_KEY = b'"type":"'


def line_type(raw: bytes) -> str | None:
    """The type of a line, read from its start without parsing it."""
    at = raw.find(_TYPE_KEY, 0, 128)
    if at < 0:
        return None
    start = at + len(_TYPE_KEY)
    end = raw.find(b'"', start, start + 80)
    return raw[start:end].decode("utf-8", "replace") if end > 0 else None


def read(path: Path, types: Iterable[str] | None = None) -> Iterator[dict[str, Any]]:
    """The lines of the log at ``path`` in order. ``types`` are the types wanted, exact or a
    prefix ending in ``.`` (everything but ``model.*`` when None). A torn last line and a line
    that does not parse are skipped; a reference to spilled data is resolved."""
    wanted = None if types is None else frozenset(types)
    prefixes = tuple(t for t in wanted or () if t.endswith("."))
    spilled: dict[str, list[str]] = {}
    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        for raw in fh:
            if not raw.endswith(b"\n"):
                return
            kind = line_type(raw)
            is_spill_part = kind == ARTIFACT and b'"kind":"spill"' in raw
            if wanted is None:
                if kind is not None and kind.startswith("model.") and not is_spill_part:
                    continue
            elif (
                kind is not None
                and kind not in wanted
                and not kind.startswith(prefixes)
                and not is_spill_part
            ):
                continue
            try:
                env = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(env, dict):
                continue
            data = env.get("data")
            if isinstance(data, dict):
                if is_spill_part:
                    spilled.setdefault(str(data.get("artifact_id")), []).append(
                        str(data.get("content", ""))
                    )
                elif data.get("spilled") is True and "artifact" in data:
                    try:
                        env["data"] = json.loads("".join(spilled.pop(str(data["artifact"]), [])))
                    except ValueError:
                        env["data"] = {}
            if wanted is not None:
                kind = str(env.get("type"))
                if kind not in wanted and not kind.startswith(prefixes):
                    continue
            yield env


_INPUT_HASH = re.compile(
    rb'^\{"seq":\d+,"id":"(e\d+)","type":"model\.input".{0,200}?"data":\{"hash":"([0-9a-f]+)"'
)


def input_hashes(path: Path) -> dict[str, str]:
    """The digest of each ``model.input`` line in the log and the id of the line, found without
    parsing the lines."""
    found: dict[str, str] = {}
    try:
        with path.open("rb") as fh:
            for raw in fh:
                if raw.endswith(b"\n") and (m := _INPUT_HASH.match(raw[:400])):
                    found[m.group(2).decode()] = m.group(1).decode()
    except OSError:
        return {}
    return found


def parts_content(parts: list[dict[str, Any]]) -> str | None:
    """The content of an artifact from its ``artifact`` lines, or None when a part is missing."""
    if not parts:
        return None
    total = parts[0].get("parts")
    by_part = {p.get("part"): p for p in parts}
    if not isinstance(total, int) or any(
        n not in by_part or "content" not in by_part[n] for n in range(total)
    ):
        return None
    return "".join(by_part[n]["content"] for n in range(total))


# ----- payload references ------------------------------------------------------ #
#
# A request repeats its context, so the payloads are written once as ``model.input`` lines,
# ``{"hash", "kind", "value"}`` with ``kind`` one of ``instructions``, ``tools`` and ``item`` (one
# element of the request's ``input``), and a request names them with
# ``{"ref": <id of the model.input line>}``. A reference is allowed only at ``model.request``
# ``data.instructions``, ``data.tools`` and each element of ``data.input``; it points backward (a
# line with a smaller ``seq`` of the same session), in one hop (a payload's value holds no
# reference), at a payload whose ``kind`` fits the location.
