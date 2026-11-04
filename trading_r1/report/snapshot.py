# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""One turn's report, frozen: the question, the answer, its sources and the data behind it.

A session's evidence spans its turns, so a report is built from a single turn: the calls that
turn made (its ``tool_log``) joined with the evidence records they produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from trading_r1.runtime.evidence import EvidenceRecord, lineage
from trading_r1.runtime.session.store import SessionStore, is_completed, is_cut_off

DATA_EARLIER = "This turn fetched no data; its figures come from earlier in the conversation."
DISCLAIMER = "For research and education only, not investment advice."
# a list item is `- [N] ...`, or `N. ...` in a log written before numbers lasted a conversation
_REFERENCES = re.compile(
    r"\n#{1,3} References\s*\n(?P<body>(?:\s*(?:\d+\.|-\s+\[\d+\])\s.*\n?)+)\s*$"
)
_ITEM = re.compile(
    r"^\s*(?:-\s+\[(?P<listed>\d+)\]|(?P<ordered>\d+)\.)\s+"
    r"(?:\[(?P<title>.*)\]\((?P<url>\S+)\)|(?P<bare>.+))$"
)


@dataclass(frozen=True)
class Source:
    """A numbered source of the answer: a page it cites."""

    index: int
    title: str
    url: str


@dataclass(frozen=True)
class DataRow:
    """One tool call the answer rests on and where its data came from."""

    evidence: str  # the record id (e1, …)
    call: str  # tool(arg=value, …)
    source: str
    as_of: str  # the date the data runs to, else when it was recorded
    ok: bool

    @property
    def label(self) -> str:
        """The record as a reader sees it: ``#1`` for ``e1``, apart from a source's ``[1]``."""
        return "#" + self.evidence[1:] if self.evidence else ""


def _cell(text: str) -> str:
    """Text that stays in one cell of a markdown table."""
    return " ".join(text.split()).replace("|", "\\|")


@dataclass(frozen=True)
class ReportSnapshot:
    question: str
    answer: str  # the markdown body, without its trailing References list
    sources: tuple[Source, ...]
    data: tuple[DataRow, ...]
    model: str
    mode: str
    created: str
    version: str
    elapsed_s: float | None = None
    cited: tuple[str, ...] = ()  # the pages the run actually read or took from its news store
    data_earlier: bool = False  # this turn called nothing: its figures are from earlier turns
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)  # the whole run

    def markdown(self) -> str:
        """The report as markdown: the answer, its References, the data calls behind it, and
        the disclaimer."""
        parts = [self.answer.rstrip()]
        if self.sources:
            refs = "\n".join(f"- [{s.index}] [{s.title}]({s.url})" for s in self.sources)
            parts.append(f"## References\n\n{refs}")
        if self.data:
            rows = "\n".join(
                "| "
                + " | ".join(
                    _cell(c)
                    for c in (r.label, r.call, r.source, r.as_of + ("" if r.ok else ", failed"))
                )
                + " |"
                for r in self.data
            )
            parts.append(
                f"## Data\n\n| Record | Call | Source | As of |\n|---|---|---|---|\n{rows}"
            )
        elif self.data_earlier:
            parts.append(f"## Data\n\n{DATA_EARLIER}")
        parts.append(DISCLAIMER)
        return "\n\n".join(parts) + "\n"


def split_references(text: str) -> tuple[str, tuple[Source, ...]]:
    """The answer without its closing ``## References`` list, and that list as sources. An answer
    with no such list is returned whole."""
    match = _REFERENCES.search(text)
    if match is None:
        return text, ()
    sources: list[Source] = []
    for line in match.group("body").splitlines():
        item = _ITEM.match(line)
        if item is None:
            continue
        url = item.group("url") or ""
        title = item.group("title") or item.group("bare") or url
        number = item.group("listed") or item.group("ordered")
        sources.append(Source(int(number), title.strip(), url))
    return text[: match.start()].rstrip() + "\n", tuple(sources)


def _call(name: str, args: dict[str, Any]) -> str:
    shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"{name}({shown if len(shown) < 160 else shown[:159] + '…'})"


def _text_of(traj: dict[str, Any]) -> str:
    """A turn's text as shown."""
    return traj.get("text") or ""


def _reportable(turn: dict[str, Any]) -> bool:
    """A turn that finished, or one a limit stopped having written something."""
    ended = turn["termination"]
    return is_completed(ended) or (is_cut_off(ended) and bool(_text_of(turn["trajectory"])))


def _row(record: dict[str, Any], call: str, ok: bool = True) -> DataRow:
    prov = record.get("provenance") or {}
    return DataRow(
        evidence=record["id"],
        call=call,
        source=str(prov.get("source") or ""),
        as_of=str(prov.get("as_of") or record.get("recorded_at") or "")[:10],
        ok=ok,
    )


def _earlier_rows(rows: list[DataRow], records: dict[str, dict[str, Any]]) -> list[DataRow]:
    """The records from earlier turns that this turn's calls were computed from by reference."""
    known = [EvidenceRecord.from_dict(r) for r in records.values()]
    ours = {r.evidence for r in rows}
    walked = {n.id for r in rows if r.evidence for n in lineage(r.evidence, known).nodes}
    return [
        _row(record, _call(record.get("tool", ""), record.get("args") or {}))
        for record in records.values()  # in the order they were recorded
        if record["id"] in walked - ours
    ]


def snapshot_from_session(
    store: SessionStore, session_id: str, turn: int | None = None
) -> ReportSnapshot | None:
    """The report of ``turn`` (the last answered turn by default), or None if there is none."""
    answered = [
        t
        for t in store.turns(session_id)
        if t.get("type") == "turn" and t.get("trajectory") and _reportable(t)
    ]
    chosen = (
        (answered[-1] if turn is None else next((t for t in answered if t["turn"] == turn), None))
        if answered
        else None
    )
    if chosen is None:
        return None
    traj = chosen["trajectory"]
    answer, sources = split_references(_text_of(traj))
    if is_cut_off(chosen["termination"]):
        answer = f"*Cut off before it finished.*\n\n{answer}"
    records = {r["id"]: r for r in store.evidence(session_id)}
    rows: list[DataRow] = []
    for call in traj.get("tool_log") or []:
        record = records.get(call.get("evidence") or "")
        if record is None:
            if call.get("ok", True) is False:  # a failed call left no record: still shown
                rows.append(
                    DataRow("", _call(call.get("name", ""), call.get("args") or {}), "", "", False)
                )
            continue
        name = _call(call.get("name", record["tool"]), call.get("args") or {})
        rows.append(_row(record, name, bool(call.get("ok", True))))
    rows = [*_earlier_rows(rows, records), *rows]
    config = chosen.get("config") or {}
    return ReportSnapshot(
        data_earlier=not rows
        and any(
            (t.get("trajectory") or {}).get("tool_log")
            for t in store.turns(session_id)
            if t.get("type") == "turn" and t["turn"] < chosen["turn"]
        ),
        question=chosen.get("question", ""),
        answer=answer,
        sources=sources,
        data=tuple(rows),
        model=str(traj.get("model") or config.get("model") or ""),
        mode=str(config.get("mode") or ""),
        created=str(chosen.get("at") or ""),
        version=str(config.get("version") or ""),
        elapsed_s=traj.get("elapsed_s"),
        cited=tuple(c["url"] for c in traj.get("citations") or [] if c.get("url")),
        raw=traj,
    )
