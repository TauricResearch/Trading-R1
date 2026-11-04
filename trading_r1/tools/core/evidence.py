# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""evidence: read back what the session recorded.

A core tool bound to the run's evidence store. With an id it returns that record: the output as
the model first read it, with its call, provenance, its links (the inputs it was derived from)
and the sources those links trace back to. With a path, it returns one
value from its payload. Without an id it lists the session's records. This is how a result cut
from the context (its message now a pointer) is restored, and how a later turn answers "what
did we use, where from, how derived".
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Text
from trading_r1.runtime.chaining import ChainFailure, resolve_one
from trading_r1.runtime.evidence import EvidenceRecord, EvidenceStore, lineage, value_paths

_LIST_MAX = 100  # the most recent records a listing shows
_FIELDS_MAX = 60  # payload values a whole-record read lists
_LIST_ITEMS = 20  # items of a list value a path read shows


class EvidenceRequest(BaseModel):
    """Input for reading the session's evidence."""

    id: str | None = Field(
        None, pattern=r"^e\d+$", description="A record's id (e.g. e12); omit to list them all."
    )
    path: str | None = Field(
        None,
        min_length=1,
        description="A path into the record's payload, as in prev:<id>.<path>, e.g. "
        "ttm.total_debt, closes[0] or closes[-30:]; omit for the whole record.",
    )


def _sources(rec: EvidenceRecord, store: EvidenceStore) -> tuple[str, ...]:
    """The records ``rec`` traces back to through its links; none when it has no links."""
    if not rec.inputs:
        return ()
    return lineage(rec.id, store.all()).sources


class Listed(BaseModel):
    id: Code
    call: Code
    recorded_at: Day


class EvidenceOutput(Output):
    """A listing of the session's records (``total``, ``records``), or one record: its call,
    when it was recorded, its provenance, the records it was derived from and those they trace
    back to, then either one ``value`` at ``path`` or the ``output`` as first returned with its
    exact values (``exact_values``, path = value)."""

    total: Count = None
    earlier_not_shown: Count = None
    records: list[Listed] = []
    id: Code = None
    call: Code = None
    recorded_at: Day = None
    provenance: dict[str, Code] = {}
    derived_from: list[Code] = []
    traces_back_to: list[Code] = []  # past the direct links: "<id> <tool>"
    path: Code = None
    value: Text = None  # the value at `path`, as JSON (a mapping as its paths)
    output: Text = None
    exact_values: Text = None


def _record(rec: EvidenceRecord, store: EvidenceStore) -> dict:
    """The fields every read of a record states."""
    sources = _sources(rec, store)
    deeper = sources and set(sources) != set(rec.inputs)  # the walk went past the links
    return {
        "id": rec.id,
        "call": rec.call,
        "recorded_at": rec.recorded_at,
        "provenance": {k: str(v) for k, v in (rec.provenance or {}).items()},
        "derived_from": list(rec.inputs),
        "traces_back_to": [f"{i} {r.tool}" for i in sources if (r := store.get(i))]
        if deeper
        else [],
    }


def _listing(store: EvidenceStore) -> Result:
    records = store.all()
    shown = records[-_LIST_MAX:]
    return Result(
        EvidenceOutput(
            total=len(records),
            earlier_not_shown=len(records) - len(shown) or None,
            records=[Listed(id=r.id, call=r.call, recorded_at=r.recorded_at) for r in shown],
        )
    )


def _value_text(value: object, path: str) -> str:
    """A value read at a path: a mapping as its paths, a long list as its head, else JSON."""
    if isinstance(value, dict):
        return "\n".join(value_paths(value, path)[:_FIELDS_MAX])
    if isinstance(value, list) and len(value) > _LIST_ITEMS:
        head = json.dumps(value[:_LIST_ITEMS], default=str, ensure_ascii=False)
        return f"{head[:-1]}, …] ({len(value)} items; read the rest as `{path}[a:b]`)"
    return json.dumps(value, default=str, ensure_ascii=False)


def build_evidence_spec(store: EvidenceStore) -> ToolSpec:
    """The per-run ``evidence`` tool, bound to the session's ``store``."""

    async def handler(req: EvidenceRequest) -> Result:
        if req.id is None:
            if req.path:
                raise ToolError("a path needs an id")
            return _listing(store)
        rec = store.get(req.id)
        if rec is None:
            latest = ", ".join(r.id for r in store.all()[-10:]) or "none"
            raise ToolError(f"no evidence with id {req.id!r} in this session (latest: {latest})")
        if req.path:
            value = resolve_one(f"prev:{rec.id}.{req.path}", store.payloads)
            if isinstance(value, ChainFailure):
                raise ToolError(value.reason)
            return Result(
                EvidenceOutput(
                    **_record(rec, store), path=req.path, value=_value_text(value, req.path)
                )
            )
        fields = value_paths(rec.payload)
        exact = "\n".join(fields[:_FIELDS_MAX])
        if len(fields) > _FIELDS_MAX:
            exact += f"\n… {len(fields) - _FIELDS_MAX} more; read one with `path`"
        return Result(
            EvidenceOutput(**_record(rec, store), output=rec.output, exact_values=exact or None)
        )

    return ToolSpec(
        name="evidence",
        display_name="core.evidence",
        result_kind="control",
        tier="core",
        description=(
            "Read back a result recorded earlier in this session (this turn or an earlier one). "
            "With `id`, the record as it was first returned, with its source, date, the ids it "
            "was derived from and the sources they trace back to; add `path` for one exact "
            "value from it. Without `id`, a list of the session's records. Use it when a result "
            "shows as cut from the context, or when asked what was used, from where, and how "
            "derived."
        ),
        request_schema=EvidenceRequest,
        handler=handler,
        output=EvidenceOutput,
        tags=("core", "meta"),
        evidence=False,
    )
