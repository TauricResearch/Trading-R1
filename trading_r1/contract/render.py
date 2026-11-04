# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The renderer: an output's JSON payload, read with its JSON Schema, into the model's text.

The one place values are formatted or rounded; the payload keeps full precision. The text is
written for the model's information per token: every value is labelled by its path in the
payload, which is what ``prev:<id>.<path>`` chains.

- a value is formatted by its unit (``contract/units.py``); amounts carry no currency sign,
  since the output's ``currency`` field states it once;
- an object's short fields share one line, ``path: key=value | …``; prose, lists and nested
  objects follow under their own paths, in declared order (a mapping's in its own order);
- a list of flat records is a table: ``path: N rows``, a header, ``a | b | c`` rows; records
  with prose or nested lists are written one by one (``path[0]: …``); a field with the same
  value in every record is stated once (``path[*]: key=value``); a mapping of flat records is
  such a table labelled by the path that reads it (``path.<key>: N rows``);
- an object with an index (``x-index``, ``Series``) renders its aligned arrays as one table,
  oriented so the longer dimension runs down the rows; per-key units of a mapping of arrays
  come from a sibling ``units`` field;
- a long table shows its head and tail and says how many rows are left out;
- a matrix with ``x-axes`` is one table, its row axis down the side and its column axis
  across the top;
- ``x-hidden`` fields stay out of the text; a hidden array is named with its size, so the
  model knows the path exists;
- a required measurement that is missing reads ``n/a``; an optional field, a label, an empty
  list or an absent section is left out;
- ``x-then``, the next step the tool suggests, closes the text.
"""

from __future__ import annotations

from typing import Any

_TABLE_ROWS = 12  # rows of a table shown before head and tail stand in for the rest
_HEAD_ROWS = 2
_INLINE_ITEMS = 12  # a list of scalars this long or shorter is written inline
_SIG = 4  # significant digits for plain numbers
_LABELS = ("code", "day", "text")  # units that name or describe rather than measure
_NOISE = 1e-12  # a value this close to zero is arithmetic noise (a solver's, a residual's): 0


def render(payload: dict[str, Any], schema: dict[str, Any]) -> str:
    """The text of a payload under its output schema."""
    out: list[str] = []
    _Walker(schema.get("$defs", {}), out).object(payload, schema, "")
    if schema.get("x-then"):
        out.append(schema["x-then"])
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# values


def _is_real(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _compact(v: float) -> str:
    a = abs(v)
    for suffix, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if a >= scale:
            return f"{v / scale:.2f}{suffix}"
    return _plain(v)


def _plain(v: float) -> str:
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    if abs(v) >= 1e6:
        return _compact(v)
    if abs(v) >= 10**_SIG:
        return f"{v:.0f}"
    return f"{v:.{_SIG}g}"


def _percent(v: float) -> str:
    """Three significant figures, never fewer than one decimal: 48.7%, 4.25%, 0.0123%."""
    p = v * 100
    if abs(p) >= 10:
        return f"{p:.1f}%"
    return f"{p:.2f}%" if abs(p) >= 0.1 or p == 0 else f"{p:.3g}%"


def _level(v: float) -> str:
    return f"{v:.2f}" if abs(v) >= 1 else f"{v:.{_SIG}g}"


_FORMATS = {
    "money": _compact,
    "count": _compact,
    "price": _level,
    "fraction": _percent,
    "percent": lambda v: f"{v:.2f}%",
    "points": lambda v: f"{v:+.2f}pp",
    "multiple": lambda v: f"{v:.2f}x",
    "number": _plain,
    "exact": lambda v: str(v) if isinstance(v, int) else f"{v:.15g}",
}


def value(v: Any, unit: str | None) -> str:
    """One value as the model reads it."""
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "true" if v else "false"
    if _is_real(v):
        return _FORMATS.get(unit or "number", _plain)(0 if abs(v) < _NOISE else v)
    if unit == "day" and isinstance(v, str) and len(v) > 16 and v[10] == "T":
        return f"{v[:10]} {v[11:16]}Z" if v.endswith("Z") else v[:16].replace("T", " ")
    return str(v)


# ---------------------------------------------------------------------------------------------
# the walk


class _Walker:
    def __init__(self, defs: dict[str, Any], out: list[str]) -> None:
        self.defs = defs
        self.out = out

    def node(self, schema: dict[str, Any], v: Any = None) -> dict[str, Any]:
        """A schema node with ``$ref`` followed and an optional's null branch dropped; of a
        union's branches, the one ``v``'s shape matches (else the first). The outer node's own
        keys (unit, hints) win over the branch's."""
        node = dict(schema)
        while True:
            if "$ref" in node:
                ref = node.pop("$ref")
                node = {**self.defs[ref.rsplit("/", 1)[-1]], **node}
            elif "anyOf" in node:
                branches = [b for b in node.pop("anyOf") if b.get("type") != "null"]
                fits = [b for b in branches if self.kind(b) == _shape(v)]
                node = {**(fits or branches or [{}])[0], **node}
            else:
                return node

    def kind(self, schema: dict[str, Any]) -> str:
        n = self.node(schema)
        if "properties" in n or isinstance(n.get("additionalProperties"), dict):
            return "object"
        return "array" if n.get("type") == "array" else "scalar"

    # --- dispatch on the payload's shape ------------------------------------------------------

    def any(
        self,
        v: Any,
        schema: dict[str, Any],
        path: str,
        required: bool = True,
        units: dict[str, str] | None = None,
    ) -> None:
        n = self.node(schema, v)
        unit = n.get("unit")
        if n.get("x-hidden"):  # named, so the model knows it can chain it
            if isinstance(v, list) and v:
                self.out.append(f"{path}: {len(v)} items, not shown")
            elif isinstance(v, dict) and v and "properties" not in n and not _labels(v):
                keys = list(v)
                more = f", … {len(keys) - _INLINE_ITEMS} more" if len(keys) > _INLINE_ITEMS else ""
                self.out.append(
                    f"{path}: keys [{', '.join(keys[:_INLINE_ITEMS])}{more}], not shown"
                )
        elif v is None or v == [] or v == {}:
            # a required measurement that is missing is stated; an optional field (one that
            # applies only sometimes), a label, a list or a section says nothing
            if required and self.kind(n) == "scalar" and unit not in _LABELS:
                self.out.append(f"{path}: n/a")
        elif isinstance(v, dict) and self.records(n):
            # a mapping of flat records is a table keyed by the mapping's keys
            items = self.node(n["additionalProperties"])
            keyed = {**items, "properties": {"key": {"unit": "code"}, **items["properties"]}}
            rows = [{"key": k, **r} for k, r in v.items() if isinstance(r, dict)]
            # labelled as the path it is read by: quotes.<key>, never quotes[0]
            self.array(rows, {"items": keyed, "x-rows": n.get("x-rows")}, f"{path}.<key>")
        elif isinstance(v, dict):
            self.object(v, n, path, units=units)
        elif isinstance(v, list):
            self.array(v, n, path)
        elif unit == "text" and "\n" in str(v):
            self.out.append(f"{path}:\n{v}")
        else:
            self.out.append(f"{path}: {value(v, unit)}")

    def records(self, node: dict[str, Any]) -> bool:
        """A mapping (no declared fields) whose values are records of short fields only."""
        rest = node.get("additionalProperties")
        if "properties" in node or not isinstance(rest, dict):
            return False
        props = self.node(rest).get("properties")
        return bool(props) and all(self.flat(self.node(p)) for p in props.values())

    def flat(self, node: dict[str, Any]) -> bool:
        """A field a table can hold: a short one, or a hidden one (left out of the table)."""
        return self.short(node) or bool(node.get("x-hidden"))

    def unit(self, node: dict[str, Any], unit: str | None) -> dict[str, Any]:
        """``node`` with its values (a scalar's, or a list's items at any depth) in ``unit``."""
        if not unit:
            return node
        if node.get("type") == "array":
            return {**node, "items": self.unit(self.node(node.get("items", {})), unit)}
        return {**node, "unit": unit}

    def short(self, node: dict[str, Any]) -> bool:
        """A field written inline: a scalar that is not prose and not hidden."""
        return (
            self.kind(node) == "scalar" and node.get("unit") != "text" and not node.get("x-hidden")
        )

    def object(
        self,
        v: dict[str, Any],
        schema: dict[str, Any],
        path: str,
        skip: frozenset = frozenset(),
        units: dict[str, str] | None = None,
    ) -> None:
        """Its short fields on one line (``path: key=value | …``), then each other field under
        its own path, in declared order; ``skip`` names fields stated already. ``units`` gives
        fields a unit known only with the value (``field`` or ``field.sub``): those inherited,
        and a ``units`` mapping of the object's own."""
        n = self.node(schema)
        own = v.get("units") if isinstance(v.get("units"), dict) else {}
        units = {**(units or {}), **{k: u for k, u in own.items() if isinstance(u, str)}}
        props: dict[str, Any] = n.get("properties", {})
        rest = n.get("additionalProperties")
        rest = rest if isinstance(rest, dict) else {}
        index_key = n.get("x-index")
        used, columns = (
            self.aligned(v, n, path) if isinstance(v.get(index_key), list) else (set(), [])
        )
        for key, p in props.items():  # a matrix states its axes
            axes = self.node(p).get("x-axes")
            if axes and _laid_out(v.get(key), v, axes):
                used = used | set(axes)
        inline, later = [], []
        for key, item in v.items():
            if key in skip or (key in used and key != index_key):
                continue
            node = self.unit(self.node(props.get(key, rest), item), units.get(key))
            required = key not in props or key in n.get("required", ())
            if key == index_key and columns:
                later.append((key, item, None, required))
            elif self.short(node):
                if item is not None:
                    inline.append(f"{key}={value(item, node.get('unit'))}")
                elif required and node.get("unit") not in _LABELS:
                    inline.append(f"{key}=n/a")  # a required measurement that is missing
            else:
                later.append((key, item, node, required))
        if inline:
            self.out.append(f"{path}: " + " | ".join(inline) if path else " | ".join(inline))
        for key, item, node, required in later:
            if node is None:
                self.series(item, _join(path, key), columns, n.get("x-rows"), path)
            elif node.get("x-axes") and _laid_out(item, v, node["x-axes"]):
                self.matrix(item, node, _join(path, key), v, props, units)
            else:
                inner = {k[len(key) + 1 :]: u for k, u in units.items() if k.startswith(key + ".")}
                self.any(item, node, _join(path, key), required, inner)

    def matrix(
        self,
        v: list[Any],
        node: dict[str, Any],
        path: str,
        parent: dict,
        props: dict,
        units: dict[str, str],
    ) -> None:
        """A list of rows against its two axes: ``rows \\ columns`` heads the table."""
        r, c = node["x-axes"]

        def unit_of(a: str) -> str | None:
            items = self.node(self.node(props.get(a, {})).get("items", {}))
            return units.get(a) or items.get("unit")

        cell = self.node(self.node(node.get("items", {})).get("items", {})).get("unit")
        head = [f"{r} \\ {c}"] + [value(x, unit_of(c)) for x in parent[c]]
        rows = [
            [value(x, unit_of(r))] + [value(y, cell) for y in (row or [None] * len(parent[c]))]
            for x, row in zip(parent[r], v, strict=True)
        ]
        self.table(path, head, rows, None)

    def array(self, v: list[Any], schema: dict[str, Any], path: str) -> None:
        first = next((x for x in v if x is not None), None)
        items = self.node(schema.get("items", {}), first)
        budget = schema.get("x-rows") or _TABLE_ROWS
        if self.kind(items) == "scalar":
            unit = items.get("unit")
            shown = schema.get("x-rows") or _INLINE_ITEMS
            if unit == "text":
                self.out += [f"{path}[{i}]: {x}" for i, x in enumerate(v)]
            elif len(v) <= shown:
                self.out.append(f"{path}: [{', '.join(value(x, unit) for x in v)}]")
            else:
                head = ", ".join(value(x, unit) for x in v[:_HEAD_ROWS])
                tail = ", ".join(value(x, unit) for x in v[-(shown - _HEAD_ROWS) :])
                self.out.append(f"{path}: {len(v)} values [{head}, … {tail}]")
            return
        props: dict[str, Any] = items.get("properties", {})
        v = [r for r in v if r is not None]  # an optional record that is absent says nothing
        dicts = [r for r in v if isinstance(r, dict)]
        # a field with one value in every record is stated once, for all of them
        same = {
            k: dicts[0].get(k)
            for k, p in props.items()
            if len(dicts) > 1
            and self.short(self.node(p))
            and dicts[0].get(k) is not None
            and all(r.get(k) == dicts[0].get(k) for r in dicts)
        }
        if same:
            line = " | ".join(
                f"{k}={value(x, self.node(props[k]).get('unit'))}" for k, x in same.items()
            )
            self.out.append(f"{path}: {line}" if path.endswith(".<key>") else f"{path}[*]: {line}")
        if props and dicts and all(self.flat(self.node(p)) for p in props.values()):
            cols = [
                k
                for k, p in props.items()
                if k not in same
                and self.short(self.node(p))
                and any(r.get(k) is not None for r in v)
            ]
            rows = [[value(r.get(k), self.node(props[k]).get("unit")) for k in cols] for r in v]
            self.table(path, cols, rows, budget)
            return
        if same:
            self.out.append(f"{path}: {len(v)} items")
        # records written one by one are what the tool returned: all of them, unless the output
        # sets a budget (a table's head and tail are for long series)
        shown = schema.get("x-rows") or len(v)
        for i, item in enumerate(v[:shown]):
            if isinstance(item, dict):
                self.object(item, items, f"{path}[{i}]", frozenset(same))
            else:
                self.any(item, items, f"{path}[{i}]")
        if len(v) > shown:
            self.out.append(f"{path}: {len(v) - shown} more items not shown")

    def aligned(self, v: dict[str, Any], n: dict[str, Any], path: str) -> tuple[set[str], list]:
        """The arrays aligned to the object's index (list fields and mappings of lists of
        its length), as (the keys they use, the columns)."""
        props: dict[str, Any] = n.get("properties", {})
        size = len(v[n["x-index"]])
        units = v.get("units") if isinstance(v.get("units"), dict) else {}
        columns: list[tuple[str, list[Any], str | None]] = []
        used: set[str] = set()
        spends_units = False
        for key, item in v.items():
            node = self.node(props.get(key, {}))
            if node.get("x-hidden") or key == n["x-index"]:
                continue
            items = self.node(node.get("items", {}))
            if (
                isinstance(item, list)
                and len(item) == size
                and self.kind(node) == "array"
                and self.short(items)
            ):
                if all(x is None for x in item):
                    used.add(key)  # a column with no values says nothing
                    continue
                unit = units.get(key) or self.node(node.get("items", {})).get("unit")
                spends_units = spends_units or key in units
                columns.append((_join(path, key), item, unit))
                used.add(key)
            elif (
                isinstance(item, dict)
                and item
                and self.kind(self.node(node.get("additionalProperties") or {}).get("items", {}))
                == "scalar"
                and all(isinstance(a, list) and len(a) == size for a in item.values())
            ):
                inner = self.node(node.get("additionalProperties") or {})
                spends_units = True
                unit = self.node(inner.get("items", {})).get("unit")
                columns += [
                    (_join(path, f"{key}.{k}"), a, units.get(k, unit))
                    for k, a in item.items()
                    if any(x is not None for x in a)
                ]
                used.add(key)
        if spends_units and units:
            used.add("units")  # spent on formatting the mapping's columns
        return used, columns

    def series(
        self, index: list[Any], index_path: str, columns: list, budget: int | None, path: str
    ) -> None:
        """Aligned arrays as one table: time down the rows, or, for a few periods of many
        lines (a statement), the lines down the rows and every line shown."""
        if len(index) >= len(columns):
            rows = [
                [str(index[i])] + [value(c[i], u) for _, c, u in columns] for i in range(len(index))
            ]
            cols = [index_path] + [k for k, _, _ in columns]
            self.table(path, cols, rows, budget or _TABLE_ROWS)
        else:
            rows = [[k] + [value(x, u) for x in c] for k, c, u in columns]
            self.table(path, [index_path] + [str(i) for i in index], rows, None)

    def table(self, path: str, cols: list[str], rows: list[list[str]], budget: int | None) -> None:
        """A header and rows; past ``budget`` rows, the head and the tail."""
        self.out.append(f"{path}: {len(rows)} rows" if path else f"{len(rows)} rows")
        if not cols:
            return  # every field stated once for all rows
        self.out.append(" | ".join(cols))
        if budget is None or len(rows) <= budget:
            self.out += [" | ".join(r) for r in rows]
            return
        tail = budget - _HEAD_ROWS
        self.out += [" | ".join(r) for r in rows[:_HEAD_ROWS]]
        self.out.append(f"… {len(rows) - budget} rows not shown")
        self.out += [" | ".join(r) for r in rows[-tail:]]


def _labels(v: dict[str, Any]) -> bool:
    """A mapping of labels (a ``units`` mapping): metadata, not a collection to name."""
    return all(isinstance(x, str) for x in v.values())


def _laid_out(m: Any, parent: dict[str, Any], axes: list[str]) -> bool:
    """A matrix whose rows and columns match its axes, as the table needs."""
    r, c = (parent.get(a) for a in axes)
    return (
        isinstance(m, list)
        and isinstance(r, list)
        and isinstance(c, list)
        and len(m) == len(r)
        and all(row is None or (isinstance(row, list) and len(row) == len(c)) for row in m)
    )


def _shape(v: Any) -> str | None:
    """The schema kind a value has: object, array or scalar (None for a missing value)."""
    if v is None:
        return None
    return "object" if isinstance(v, dict) else "array" if isinstance(v, list) else "scalar"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key
