# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Argument coercion: repair model-emitted args before validation.

A boundary concern at the dispatcher: some models emit a *structured* argument
(list/dict/nested object) as a JSON-encoded **string**, or wrap a lone ``prev:``
reference in a 1-element list. Both repairs are **schema-driven** and run
before chaining + validation. Neither raises: an unrepairable value is left
as-is and validation surfaces the path-aware error the model self-corrects on.

The schema is read as **JSON Schema**, the one shape every tool produces
(``Args.json_schema()``), so the repairs never depend on how a tool declared it.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from trading_r1.contract.args import Args
from trading_r1.runtime.chaining import REF_PREFIX

_SCALAR_TYPES = frozenset({"string", "number", "integer", "boolean", "null"})
# How Optional/Union render in JSON Schema: inspect each member.
_UNION_KEYS = ("anyOf", "oneOf", "allOf")
_MAX_DEPTH = 16  # bounds recursion over union members and nested refs, so a cyclic
# `$defs` terminates instead of raising past the dispatcher's never-raise guarantee.


def _properties(schema: Args) -> tuple[dict[str, Any], dict[str, Any]]:
    """The top-level ``properties`` map and the ``$defs`` used to resolve ``$ref``s, or
    empties when the schema has none / is odd."""
    try:
        js = schema.json_schema()
    except Exception:  # noqa: BLE001 (a broken schema must not break coercion)
        return {}, {}
    if not isinstance(js, dict):
        return {}, {}
    props = js.get("properties")
    defs = js.get("$defs")
    return (
        props if isinstance(props, dict) else {},
        defs if isinstance(defs, dict) else {},
    )


def _deref(node: Any, defs: dict[str, Any]) -> Any:
    """Follow a chain of ``#/$defs/<name>`` ``$ref``s to the definition it ultimately names,
    so a field is classified by what it *refers to* (a nested object, or a scalar/enum), not
    just by carrying a ref. A visited set makes a cyclic chain terminate; an unresolvable ref
    is returned unchanged (and then reads as neither container nor scalar, the safe default)."""
    seen: set[str] = set()
    while isinstance(node, dict):
        ref = node.get("$ref")
        if not (isinstance(ref, str) and ref.startswith("#/$defs/")):
            return node
        name = ref.split("/")[-1]
        target = defs.get(name)
        if name in seen or not isinstance(target, dict):
            return node  # cycle or unresolvable: stop at the ref node
        seen.add(name)
        node = target
    return node


def _is_null(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


def _has_type(node: dict[str, Any], *names: str) -> bool:
    """True when the node declares (one of) ``names`` as its JSON Schema ``type``."""
    t = node.get("type")
    return t in names or (isinstance(t, list) and any(x in names for x in t))


def _expects_container(node: Any, defs: dict[str, Any], depth: int = 0) -> bool:
    """True when a property's JSON Schema describes a structured container the model might
    have JSON-stringified: an array or object (a ``$ref`` is resolved first, so a ref to a
    nested object counts but a ref to a scalar/enum does not), or the same nested inside an
    anyOf/oneOf/allOf (how Optional/Union render). The caller only
    json-parses strings that already start with ``[``/``{`` and leaves failures untouched, so
    a wider match here only enables a repair."""
    if depth > _MAX_DEPTH:
        return False
    node = _deref(node, defs)
    if not isinstance(node, dict):
        return False
    for key in _UNION_KEYS:
        members = node.get(key)
        if isinstance(members, list) and any(
            _expects_container(m, defs, depth + 1) for m in members
        ):
            return True
    if _has_type(node, "array", "object"):
        return True
    # A schema that structurally is an array/object but omits an explicit `type`.
    return any(k in node for k in ("items", "prefixItems", "properties", "additionalProperties"))


def _is_scalar(node: Any, defs: dict[str, Any], depth: int = 0) -> bool:
    """True only when the node is *definitely* a scalar (string/number/integer/boolean/null,
    or a union of those; a ``$ref`` is resolved first, so a ref to an enum def counts). Unknown
    / typeless / container nodes are conservatively False, so a ``[<ref>]`` wrapper is
    unwrapped only when the element type is a known scalar."""
    if depth > _MAX_DEPTH:
        return False
    node = _deref(node, defs)
    if not isinstance(node, dict):
        return False
    for key in _UNION_KEYS:
        members = node.get(key)
        if isinstance(members, list):
            non_null = [m for m in members if not _is_null(_deref(m, defs))]
            return bool(non_null) and all(_is_scalar(m, defs, depth + 1) for m in non_null)
    if "$ref" in node:  # an unresolvable ref: can't confirm scalar
        return False
    # A malformed `type` can be any JSON value (unhashable dict/list included), so restrict
    # the frozenset membership to strings: set lookup on an unhashable value would raise.
    t = node.get("type")
    if isinstance(t, list):
        return bool(t) and all(isinstance(x, str) and x in _SCALAR_TYPES for x in t)
    return isinstance(t, str) and t in _SCALAR_TYPES


def _is_list_of_scalar(node: Any, defs: dict[str, Any], depth: int = 0) -> bool:
    """True when the property is an array whose items are scalars (not array/object): the
    shape where a ``[<prev: ref>]`` wrapper should be unwrapped to the bare ref so the chaining
    resolver expands it once into a ``list[scalar]``. Optional/Union-wrapped arrays are seen
    through; a bare/typeless array stays conservatively wrapped."""
    if depth > _MAX_DEPTH:
        return False
    node = _deref(node, defs)
    if not isinstance(node, dict):
        return False
    for key in _UNION_KEYS:
        members = node.get(key)
        if isinstance(members, list):
            non_null = [m for m in members if not _is_null(_deref(m, defs))]
            if any(_is_list_of_scalar(m, defs, depth + 1) for m in non_null):
                return True
    if not _has_type(node, "array") and "items" not in node:
        return False
    items = node.get("items")
    return _is_scalar(items, defs, depth + 1) if isinstance(items, dict) else False


def coerce_args(args: Any, schema: Args) -> Any:
    """Repair model-emitted arguments against ``schema``'s JSON Schema; returns a copy.

    A string destined for a container-typed field that starts with ``[`` or ``{`` is parsed
    back to the container (scalars and non-JSON strings pass through); then a one-element list
    holding a ``prev:`` reference, for a ``list[<scalar>]`` field, is unwrapped to the bare
    reference, so the chaining resolver expands it once into a ``list[scalar]`` instead of
    element-by-element into ``list[list[...]]``. A length-1 list under an array-of-array or
    array-of-object field stays wrapped."""
    if not isinstance(args, dict):
        return args
    props, defs = _properties(schema)
    if not props:
        return args
    out = dict(args)
    for name, node in props.items():
        if name not in out:
            continue
        value = out[name]
        if isinstance(value, str) and _expects_container(node, defs):
            text = value.strip()
            if text and text[0] in "[{":
                # unparseable (incl. pathologically nested): left for validation
                with contextlib.suppress(ValueError, RecursionError):
                    value = out[name] = json.loads(text)
        if isinstance(value, list) and len(value) == 1:
            elem = value[0]
            if (
                isinstance(elem, str)
                and elem.startswith(REF_PREFIX)
                and _is_list_of_scalar(node, defs)
            ):
                out[name] = elem
    return out
