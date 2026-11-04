# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Reference-based chaining: resolve ``prev:<id>.<path>`` arg strings.

When a tool argument is ``prev:<id>.<path>``, the runtime resolves it against the payload of
an evidence record (``ToolSessionState.evidence``, runtime/evidence.py) *before* the
consumer's schema validates, so
a compute tool can consume the full series from a prior data call without
round-tripping bulk data through the LLM's context.

Path syntax (JSONPath-lite): ``prev:<id>`` (whole dict), ``.field`` (dict key),
``[N]`` (index), ``[a:b]`` (slice), ``[*]`` (map over a list); tokens compose. Filters, recursive
descent, and nested ``[*]`` are not supported. Never raises:
failures return a structured :class:`ChainFailure` the dispatcher turns into an
``ok=False`` result the model self-corrects on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

REF_PREFIX = "prev:"


@dataclass(frozen=True)
class ChainFailure:
    """A single resolution failure with enough context for the model to fix it. It is returned,
    never raised."""

    reference: str
    reason: str


def is_reference(value: Any) -> bool:
    """True when ``value`` is a reference-chaining string."""

    return isinstance(value, str) and value.startswith(REF_PREFIX)


_TOKEN_RE = re.compile(r"^(?P<key>[^.\[\]]+)(?P<indexers>(?:\[[^\]]*\])*)$")
_INDEXER_RE = re.compile(r"\[([^\]]*)\]")
_SLICE_RE = re.compile(r"^(-?\d+)?:(-?\d+)?$")


@dataclass(frozen=True)
class _Step:
    key: str
    indexers: tuple[str, ...]  # each "*", a decimal int or an a:b slice, as str


@dataclass(frozen=True)
class _ParsedRef:
    call_id: str
    steps: tuple[_Step, ...]
    raw: str


def _parse_reference(raw: str) -> _ParsedRef | ChainFailure:
    if not raw.startswith(REF_PREFIX):
        return ChainFailure(raw, "reference must start with 'prev:'")
    body = raw[len(REF_PREFIX) :]
    if not body:
        return ChainFailure(raw, "missing call_id after 'prev:'")
    call_id, path = body.split(".", 1) if "." in body else (body, "")
    if not call_id:
        return ChainFailure(raw, "empty call_id between 'prev:' and '.'")
    if not path:
        return _ParsedRef(call_id=call_id, steps=(), raw=raw)

    steps: list[_Step] = []
    for tok in path.split("."):
        if not tok:
            return ChainFailure(raw, "empty path segment (stray '.'?)")
        m = _TOKEN_RE.match(tok)
        if not m:
            return ChainFailure(raw, f"could not parse path segment {tok!r}")
        indexers: list[str] = []
        for im in _INDEXER_RE.finditer(m.group("indexers") or ""):
            inner = im.group(1)
            if inner == "*":
                indexers.append("*")
            elif inner.lstrip("-").isdigit() or _SLICE_RE.match(inner):
                indexers.append(inner)
            else:
                return ChainFailure(
                    raw,
                    f"unsupported indexer '[{inner}]' (only [N], [a:b] and [*] are supported)",
                )
        steps.append(_Step(key=m.group("key"), indexers=tuple(indexers)))
    return _ParsedRef(call_id=call_id, steps=tuple(steps), raw=raw)


def _list_dict_keys(obj: Any) -> list[str]:
    return sorted(map(str, obj.keys())) if isinstance(obj, dict) else []


def _descend_key(cur: Any, key: str, raw: str) -> Any | ChainFailure:
    if not isinstance(cur, dict):
        return ChainFailure(raw, f"field '{key}': expected dict, got {type(cur).__name__}")
    if key in cur:
        return cur[key]
    if key.lstrip("-").isdigit() and int(key) in cur:  # int-typed dict keys
        return cur[int(key)]
    return ChainFailure(
        raw, f"no field '{key}' on resolved object (available: {_list_dict_keys(cur)})"
    )


def _keyed(key: str, cur: dict[Any, Any]) -> str:
    return f"'{key}' is keyed, not a list: use {key}.<key> (available: {_list_dict_keys(cur)})"


def _index(cur: Any, ix: str, key: str, raw: str) -> Any | ChainFailure:
    if isinstance(cur, dict):
        return ChainFailure(raw, _keyed(key, cur))
    if not isinstance(cur, list):
        return ChainFailure(raw, f"'[{ix}]' on '{key}' requires a list, got {type(cur).__name__}")
    if found := _SLICE_RE.match(ix):
        low, high = found.groups()
        return cur[int(low) if low else None : int(high) if high else None]
    idx = int(ix)
    if idx < -len(cur) or idx >= len(cur):
        return ChainFailure(raw, f"'[{ix}]' on '{key}': index out of range (length {len(cur)})")
    return cur[idx]


def _dotted_key(cur: Any, steps: tuple[_Step, ...]) -> tuple[_Step, ...] | None:
    """``steps`` with the first ones joined, when the dict's key itself holds dots (``BRK.B``)."""
    if not isinstance(cur, dict) or steps[0].key in cur:
        return None
    for end in range(1, len(steps)):
        if steps[end - 1].indexers:
            return None
        joined = ".".join(step.key for step in steps[: end + 1])
        if joined in cur:
            return (_Step(key=joined, indexers=steps[end].indexers), *steps[end + 1 :])
    return None


def _walk(obj: Any, steps: tuple[_Step, ...], raw: str) -> Any | ChainFailure:
    cur: Any = obj
    for step_i, step in enumerate(steps):
        if (joined := _dotted_key(cur, steps[step_i:])) is not None:
            return _walk(cur, joined, raw)
        cur = _descend_key(cur, step.key, raw)
        if isinstance(cur, ChainFailure):
            return cur
        for pos, ix in enumerate(step.indexers):
            if ix == "*":
                if isinstance(cur, dict):
                    return ChainFailure(raw, _keyed(step.key, cur))
                if not isinstance(cur, list):
                    return ChainFailure(
                        raw, f"'[*]' on '{step.key}' requires a list, got {type(cur).__name__}"
                    )
                remaining = step.indexers[pos + 1 :]
                if "*" in remaining:
                    return ChainFailure(raw, "nested '[*]' wildcards are not supported")
                tail = (_Step(key="__tail__", indexers=remaining), *steps[step_i + 1 :])
                out: list[Any] = []
                for elem in cur:
                    sub = _apply_tail(elem, tail, raw)
                    if isinstance(sub, ChainFailure):
                        return sub
                    out.append(sub)
                return out
            cur = _index(cur, ix, step.key, raw)
            if isinstance(cur, ChainFailure):
                return cur
    return cur


def _apply_tail(item: Any, tail_steps: tuple[_Step, ...], raw: str) -> Any | ChainFailure:
    cur: Any = item
    head, *rest = tail_steps
    for ix in head.indexers:
        if ix == "*":
            return ChainFailure(raw, "nested '[*]' wildcards are not supported")
        cur = _index(cur, ix, "[*]", raw)
        if isinstance(cur, ChainFailure):
            return cur
    return _walk(cur, tuple(rest), raw) if rest else cur


# a reference inside text: dotted keys and literal indexers; a computed index (``[i]``) and
# anything glued to a preceding word stay with the text
_EMBEDDED_RE = re.compile(r"(?<![\w.])prev:e\d+(?:\.\w+|\[(?:(?:-?\d+)?:(?:-?\d+)?|-?\d+|\*)\])*")


def embed_references(
    args: dict[str, Any], text_field: str, values_field: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """Lift each ``prev:`` reference written inside ``args[text_field]`` (a calc expression,
    ``(prev:e1.ttm.total_debt - prev:e1.ttm.total_cash) / prev:e1.ttm.ebitda``), or inside an
    expression among ``args[values_field]``, into a named entry of the values (the name spells
    the reference, ``e1_ttm_total_debt``), so it resolves and is recorded as an input like any
    whole-argument reference. Lifted entries come first, so the values' own expressions can
    use them. Returns the new args and the lifted ``{name: reference}``; ``args`` unchanged
    when there are none, or when the values aren't a map (validation reports that). Never
    mutates the input."""
    given = args.get(values_field)
    if given is not None and not isinstance(given, dict):
        return args, {}
    given = given or {}
    lifted: dict[str, str] = {}

    def _name(m: re.Match[str]) -> str:
        ref = m.group(0)
        for name, known in lifted.items():
            if known == ref:
                return name
        base = name = re.sub(r"\W+", "_", ref[len(REF_PREFIX) :]).strip("_")
        n = 2
        while name in given or name in lifted:
            name, n = f"{base}_{n}", n + 1
        lifted[name] = ref
        return name

    def _embed(text: Any) -> Any:
        if not isinstance(text, str) or _EMBEDDED_RE.fullmatch(text.strip()):
            return text  # not text, or a whole reference (resolved as is)
        return _EMBEDDED_RE.sub(_name, text)

    text = _embed(args.get(text_field))
    values = {k: _embed(v) for k, v in given.items()}
    if not lifted:
        return args, {}
    return {**args, text_field: text, values_field: {**lifted, **values}}, lifted


def non_numeric(values: dict[str, Any], lifted: dict[str, str]) -> list[ChainFailure]:
    """A reference inside text stands for a number or a series of numbers; one that resolved
    to anything else (a date string, a flag, a table) is an error, not an expression to parse."""

    def _number(v: Any) -> bool:
        return isinstance(v, int | float) and not isinstance(v, bool)

    errors = []
    for name, ref in lifted.items():
        v = values.get(name)
        if not (_number(v) or (isinstance(v, list) and all(_number(x) for x in v))):
            shown = json.dumps(v, default=str)[:80]
            errors.append(ChainFailure(ref, f"resolved to {shown}, not a number or a series"))
    return errors


def referenced_ids(args: Any) -> tuple[str, ...]:
    """The evidence ids ``args`` references through ``prev:``, in order, once each: a derived
    record's ``inputs``."""
    found: list[str] = []

    def _scan(node: Any) -> None:
        if is_reference(node):
            parsed = _parse_reference(node)
            if not isinstance(parsed, ChainFailure) and parsed.call_id not in found:
                found.append(parsed.call_id)
        elif isinstance(node, dict):
            for v in node.values():
                _scan(v)
        elif isinstance(node, list):
            for v in node:
                _scan(v)

    _scan(args)
    return tuple(found)


def resolve_one(ref: str, tool_results: dict[str, dict[str, Any]]) -> Any | ChainFailure:
    """Resolve a single ``prev:`` reference against the chain map."""
    parsed = _parse_reference(ref)
    if isinstance(parsed, ChainFailure):
        return parsed
    if parsed.call_id not in tool_results:
        return ChainFailure(
            ref,
            f"no evidence with id '{parsed.call_id}' in this session "
            f"(latest: {list(tool_results)[-10:]})",
        )
    value = _walk(tool_results[parsed.call_id], parsed.steps, ref)
    if value is None:  # a field the payload has but this result could not fill
        return ChainFailure(ref, "the value is null in that result (not available for it)")
    return value


def resolve_references(
    args: Any, tool_results: dict[str, dict[str, Any]]
) -> tuple[Any, list[ChainFailure]]:
    """Recursively substitute every ``prev:`` string in ``args``. Returns
    ``(resolved_args, errors)``; a non-empty ``errors`` means the dispatcher
    should short-circuit before validation."""
    errors: list[ChainFailure] = []

    def _walk_args(node: Any) -> Any:
        if is_reference(node):
            result = resolve_one(node, tool_results)
            if isinstance(result, ChainFailure):
                errors.append(result)
                return node
            return result
        if isinstance(node, dict):
            return {k: _walk_args(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk_args(v) for v in node]
        return node

    return _walk_args(args), errors


def format_chain_errors(errors: list[ChainFailure]) -> str:
    """Render ChainErrors as one actionable markdown block for an ok=False result."""
    if not errors:
        return ""
    lines = ["chaining failed: could not resolve reference(s):"]
    lines.extend(f"  {e.reference}: {e.reason}" for e in errors)
    return "\n".join(lines)
