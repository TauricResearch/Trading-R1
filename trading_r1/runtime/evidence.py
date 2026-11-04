# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Evidence: the canonical record of what the harness used.

Every successful call of an evidence-producing tool (data, compute, web)
becomes one immutable record: an id the harness assigns (``e1``, ``e2``, … per session;
provider call ids repeat), the tool and its arguments, the structured payload, the output the
model read (the artifact, so a cut result is restorable as it was), provenance (the
tool's scalar meta: source, method; when it was recorded; which run), and
``inputs`` (the evidence ids its arguments referenced through ``prev:``). The dispatcher writes
records, never the model. The ``inputs`` links are the provenance graph; :func:`lineage` walks
them from a record back to its sources.

The store lives as long as the session and is saved with it, so
a value used in one turn resolves by id in the next (``prev:e7.ttm.total_debt``), whether or
not the tool message that first carried it is still in the context. The context is a view of
this state: a tool message cut from it becomes the record's :meth:`~EvidenceRecord.pointer`,
and the ``evidence`` tool reads the record back. Compaction never decides what is kept.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

_LOG = logging.getLogger(__name__)
CUT_MARK = "output cut from context"  # every cut tool message starts with "[" + this
_ID_RE = re.compile(r"^e(\d+)$")
_CALL_CHARS = 160  # a call's argument summary in pointers and listings


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class EvidenceRecord:
    """One recorded tool result."""

    id: str
    tool: str
    args: dict[str, Any]
    payload: dict[str, Any]
    output: str = ""
    inputs: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    run: str = ""
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "inputs": list(self.inputs),
        }

    @property
    def call(self) -> str:
        """The call in one line, ``tool(arg=value, …)``, long arguments shortened."""
        args = ", ".join(f"{k}={json.dumps(v, default=str)}" for k, v in self.args.items())
        if len(args) > _CALL_CHARS:
            args = args[: _CALL_CHARS - 1] + "…"
        return f"{self.tool}({args})"

    def pointer(self) -> str:
        """What the tool message carrying this record becomes when it is cut from the context."""
        return (
            f'[{CUT_MARK}: {self.id} / {self.call}. evidence(id="{self.id}") restores it; '
            f"prev:{self.id}.<path> passes its values on]"
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceRecord:
        """A stored record; unknown keys are ignored and missing ones take their defaults."""
        known = {f.name for f in dataclasses.fields(cls)}
        kept = {k: v for k, v in d.items() if k in known}
        return cls(
            **{
                "tool": "",
                "args": {},
                "payload": {},
                **kept,
                "inputs": tuple(d.get("inputs") or ()),
            }
        )


class EvidenceStore:
    """Append-only records for one session, addressable by id."""

    def __init__(self, records: list[EvidenceRecord] | None = None) -> None:
        self._records: dict[str, EvidenceRecord] = {r.id: r for r in records or []}
        self._observers: list[Callable[[EvidenceRecord], None]] = []
        # ids continue after the highest stored one, so a gap or a duplicate never reuses one
        self._last = max((int(m[1]) for i in self._records if (m := _ID_RE.match(i))), default=0)

    def add(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        payload: dict[str, Any],
        meta: dict[str, Any],
        output: str = "",
        inputs: tuple[str, ...] = (),
        run: str = "",
    ) -> EvidenceRecord:
        """Record a result under the next id. The payload is copied, so a record never changes
        after the fact; provenance keeps the meta's scalars (source, method…), not
        bulk artifacts."""
        self._last += 1
        record = EvidenceRecord(
            id=f"e{self._last}",
            tool=tool,
            args=copy.deepcopy(args),
            payload=copy.deepcopy(payload),
            output=output,
            inputs=inputs,
            provenance={
                k: v
                for k, v in meta.items()
                if isinstance(v, str | int | float | bool) and k != "elapsed_ms"
            },
            run=run,
            recorded_at=_now(),
        )
        self._records[record.id] = record
        for observer in list(self._observers):
            try:
                observer(record)
            except Exception:  # noqa: BLE001 (a side effect, the log, never sinks the run)
                _LOG.warning("evidence observer failed for %s", record.id, exc_info=True)
        return record

    def observe(self, observer: Callable[[EvidenceRecord], None]) -> Callable[[], None]:
        """Call `observer` with each record as it is made (the session log appends it then, so
        a killed turn keeps its evidence). Returns the function that stops observing."""
        self._observers.append(observer)
        return lambda: self._observers.remove(observer)

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        return self._records.get(evidence_id)

    @property
    def payloads(self) -> dict[str, dict[str, Any]]:
        """Each record's payload by id: what ``prev:`` references resolve against."""
        return {i: r.payload for i, r in self._records.items()}

    def all(self) -> list[EvidenceRecord]:
        return list(self._records.values())

    def __len__(self) -> int:
        return len(self._records)

    def to_list(self, since: int = 0) -> list[dict[str, Any]]:
        """The records as dicts, in the order added; ``since`` skips the first ones (a run's
        own records are those added after its start)."""
        return [r.to_dict() for r in list(self._records.values())[since:]]

    @classmethod
    def from_list(cls, items: list[dict[str, Any]] | None) -> EvidenceStore:
        return cls([EvidenceRecord.from_dict(d) for d in items or []])


DERIVED_FROM = "derived_from"  # a record's inputs: the values it was computed from


@dataclass(frozen=True)
class LineageNode:
    """A record reached by a lineage walk."""

    id: str
    tool: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class LineageEdge:
    """``source`` rests on ``target``: it was computed from it."""

    source: str
    target: str
    kind: str  # DERIVED_FROM


@dataclass(frozen=True)
class Lineage:
    """What a record rests on: the records reachable through its links (itself first), the
    links, and the ids linked to that aren't among the records (``missing``)."""

    root: str
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]
    missing: tuple[str, ...] = ()

    @property
    def sources(self) -> tuple[str, ...]:
        """The records it bottoms out in: those linking to no record we have (data, web pages,
        or a record whose links all point to missing ids)."""
        linking = {e.source for e in self.edges if e.target not in self.missing}
        return tuple(n.id for n in self.nodes if n.id not in linking)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "sources": list(self.sources)}


def lineage(evidence_id: str, records: Iterable[EvidenceRecord]) -> Lineage:
    """Walk a record's links back to its sources, breadth first: a view over the records, not
    a store. Each record is visited once, so a cycle ends the walk instead of looping; a linked
    id not among ``records`` is listed in ``missing`` (its edge kept), never raised."""
    by_id = {r.id: r for r in records}
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []
    missing: list[str] = []
    seen, queue = {evidence_id}, [evidence_id]
    while queue:
        current = queue.pop(0)
        rec = by_id.get(current)
        if rec is None:
            missing.append(current)
            continue
        nodes.append(LineageNode(rec.id, rec.tool, dict(rec.provenance)))
        for target in rec.inputs:
            edges.append(LineageEdge(rec.id, target, DERIVED_FROM))
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return Lineage(evidence_id, tuple(nodes), tuple(edges), tuple(missing))


_INLINE_ITEMS = 8  # a list of plain values this short is shown whole
_EXPAND_ITEMS = 3  # items of a list of objects shown in full; the rest by path


def value_paths(payload: Any, prefix: str = "", *, expand: int | None = _EXPAND_ITEMS) -> list[str]:
    """A payload's values by path, exact as recorded. Every key is named before any list of
    objects is opened, so a cap on the listing never hides a key: ``path = value`` for a
    scalar; a list of plain values whole when short, else ``path: n items``; a list of objects
    (pages, rows, strikes) as ``path: n items`` first, then its first items field by field
    (``pages[0].summary = …``); the rest are addressed as ``path[i]``; ``expand=None`` opens
    them all."""
    head: list[str] = []
    tail: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(value, list) and any(isinstance(x, dict | list) for x in value):
            head.append(f"{path}: {len(value)} items")
            for i, item in enumerate(value[:expand]):
                tail.extend(value_paths(item, f"{path}[{i}]", expand=expand))
        elif isinstance(value, list) and len(value) > _INLINE_ITEMS:
            head.append(f"{path}: {len(value)} items")
        else:
            head.append(f"{path} = {json.dumps(value, default=str, ensure_ascii=False)}")

    walk(payload, prefix)
    return head + tail
