# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The `calc` tool: exact arithmetic for the model.

A safe AST evaluator over numbers, the standard operators, and a small whitelist of functions
and constants.

A list of expressions evaluates element-wise (``[5.64/4.37, 28.09/29.76]``) and a dict labels its
results (``{"OCF/NI FY26": 102.72/120.07}``); their items may be quoted expressions too. Named
``values`` (numbers, series, often ``prev:`` references to earlier results, or expressions over
earlier values) can be used in the expression, a series indexed (``rev[-1]/rev[0] - 1``) or
aggregated (``mean(rev)``); ``ln`` is spelled out. A ``prev:`` reference may also sit right in the
expression: the dispatcher lifts it into ``values`` (``ToolSpec.inline_refs``), so a figure from
an earlier result enters exact, and the result's record lists where it came from.
Powers run in floating point, so a runaway exponent overflows instead of hanging.
"""

from __future__ import annotations

import ast
import math
import operator
import statistics
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Exact
from trading_r1.tools.compute.cashflows import irr, npv
from trading_r1.tools.compute.series import MAX_SERIES_LEN

_DESCRIPTION = (
    "Evaluate arithmetic exactly (growth rates, ratios, percentages, sums), e.g. "
    "'(37-13)/13*100' for a percent change. `prev:` references sit right in the expression. "
    "Operators + - * / ** % // and parentheses; functions abs, round, min, max, sqrt, exp, ln "
    "(= log), log10, floor, ceil, sum, mean, median, len, stdev and var (sample), npv(rate, "
    "flows) and irr(flows) "
    "(the first flow is at time 0); constants pi, e. Many at once: a list "
    "'[5.64/4.37, 28.09/29.76]', or a dict to label them '{\"OCF/NI FY26\": ocf/ni}'; a list of "
    "lists for a grid (no comprehensions). `values` names inputs: numbers, series "
    "(values={'rev': 'prev:<id>.lines.revenue'}, expression 'rev[-1]/rev[0] - 1') or "
    "expressions over earlier values; names must be identifiers (ocf_fy26, not 'OCF FY26'). "
    "Index a series with [i] (negative from the end), or pass it to sum/mean/median/stdev/var/"
    "min/max/len/npv/irr."
)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: lambda a, b: math.pow(a, b),  # floats: overflows fast, never a huge int
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_SCALAR_FUNCS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "floor": math.floor,
    "ceil": math.ceil,
}
# These also take one series (a list-valued name) as their argument.
_SERIES_FUNCS = {
    "min": min,
    "max": max,
    "sum": sum,
    "mean": statistics.fmean,
    "median": statistics.median,
    "stdev": statistics.stdev,  # sample (n-1), Excel's STDEV.S
    "var": statistics.variance,  # sample (n-1), Excel's VAR.S
    "len": len,
}

_FLOW_FUNCS = {"npv", "irr"}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_RESERVED = (
    frozenset(_SCALAR_FUNCS) | frozenset(_SERIES_FUNCS) | _FLOW_FUNCS | frozenset(_CONSTANTS)
)
_MAX_ITEMS = 200  # expressions in one list: plenty for one call, bounded for safety
_SUPPORTED = (
    "numbers, + - * / ** % //, (), a top-level [list] (rows may be [lists], for a grid) or "
    "{'label': expr or [list]}, names from `values` (series indexed with [i]), pi, e, and "
    "abs/round/min/max/sqrt/exp/ln/log/log10/floor/ceil/sum/mean/median/stdev/var/len/npv/irr"
)

Num = float | int
Value = Num | list[Num]
Row = list[Num] | Num
Answer = dict[str, Row] | list[Row] | Num


class _Eval:
    """Evaluates one parsed expression against named values."""

    def __init__(self, values: dict[str, Value]) -> None:
        self.values = values

    def scalar(self, node: ast.AST) -> Num:
        v = self.any(node)
        if isinstance(v, list):
            raise ToolError(
                "a series can only be indexed (x[0], x[-1]) or passed to sum/mean/median/stdev/var/"
                "min/max/len/npv/irr"
            )
        return v

    def item(self, node: ast.AST) -> Num:
        """One item of a list or dict: an expression, or a quoted expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self.scalar(_parse(node.value))
        return self.scalar(node)

    def row(self, node: ast.AST) -> Row:
        """An item, or one level of [list]: a schedule, or a row of a grid."""
        if isinstance(node, ast.List | ast.Tuple):
            return [self.item(x) for x in node.elts]
        return self.item(node)

    def any(self, node: ast.AST) -> Value:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.values:
                return self.values[node.id]
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ToolError(f"unknown name '{node.id}': pass it in `values`")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](self.scalar(node.left), self.scalar(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](self.scalar(node.operand))
        if isinstance(node, ast.Subscript):
            return self._index(node)
        if isinstance(node, ast.List | ast.Tuple):  # a series written out, as a function's argument
            return [self.scalar(x) for x in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            return self._call(node.func.id, node.args)
        raise ToolError(f"unsupported expression: {_SUPPORTED}")

    def _index(self, node: ast.Subscript) -> Num:
        series = self.any(node.value)
        if not isinstance(series, list):
            raise ToolError("only a series from `values` can be indexed")
        i = self.scalar(node.slice)
        if not float(i).is_integer() or not -len(series) <= int(i) < len(series):
            raise ToolError(f"index {i} is outside a series of {len(series)} values")
        return series[int(i)]

    def series(self, node: ast.AST) -> list[Num]:
        v = self.any(node)
        if not isinstance(v, list):
            raise ToolError("expected a series (a name from `values`, or a [list] of numbers)")
        return v

    def _call(self, name: str, args: list[ast.AST]) -> Num:
        if name == "npv":
            if len(args) != 2:
                raise ToolError("npv(rate, flows) takes a rate and a series of flows")
            return npv(self.scalar(args[0]), self.series(args[1]))
        if name == "irr":
            if len(args) != 1:
                raise ToolError("irr(flows) takes one series of flows")
            return irr(self.series(args[0]))
        if name in _SERIES_FUNCS:
            if len(args) == 1 and isinstance(value := self.any(args[0]), list):
                if not value:
                    raise ToolError(f"{name}() of an empty series")
                return _SERIES_FUNCS[name](value)
            if name in ("len", "mean", "median", "stdev", "var", "sum"):
                return _SERIES_FUNCS[name]([self.scalar(a) for a in args])
            return _SERIES_FUNCS[name](*(self.scalar(a) for a in args))
        if name in _SCALAR_FUNCS:
            return _SCALAR_FUNCS[name](*(self.scalar(a) for a in args))
        raise ToolError(f"unknown function '{name}': {_SUPPORTED}")


def _parse(expression: str) -> ast.AST:
    try:
        return ast.parse(expression.strip(), mode="eval").body
    except SyntaxError as err:
        raise ToolError(f"cannot parse '{expression}': {err.msg}") from err
    except (RecursionError, MemoryError) as err:  # nesting deeper than the parser's stack
        raise ToolError("the expression is nested too deeply") from err


def _resolve(values: dict[str, Any]) -> dict[str, Value]:
    """Named values, an expression string evaluated against the values before it."""
    resolved: dict[str, Value] = {}
    for name, value in values.items():
        resolved[name] = _Eval(resolved).scalar(_parse(value)) if isinstance(value, str) else value
    return resolved


def _finite(answer: Answer) -> Answer:
    """The answer, once every number in it is finite (``1e308*10`` is not a result)."""
    rows = list(answer.values()) if isinstance(answer, dict) else answer
    for row in rows if isinstance(rows, list) else [rows]:
        if not all(math.isfinite(x) for x in (row if isinstance(row, list) else [row])):
            raise ToolError("the result is not a finite number (it overflows or is undefined)")
    return answer


def safe_eval(expression: str, values: dict[str, Any] | None = None) -> Answer:
    """Evaluate an arithmetic expression safely: a top-level list evaluates element-wise and a
    dict by label. Raises ToolError on anything unsupported or undefined (division by zero,
    overflow)."""
    body = _parse(expression)
    try:
        ev = _Eval(_resolve(values or {}))
        items = (
            body.elts
            if isinstance(body, ast.List | ast.Tuple)
            else body.values
            if isinstance(body, ast.Dict)
            else None
        )
        if items is not None and len(items) > _MAX_ITEMS:
            raise ToolError(f"at most {_MAX_ITEMS} expressions per call")
        if isinstance(body, ast.List | ast.Tuple):
            return _finite([ev.row(item) for item in body.elts])
        if isinstance(body, ast.Dict):
            labels = [k.value if isinstance(k, ast.Constant) else None for k in body.keys]
            if not all(isinstance(label, str) for label in labels):
                raise ToolError("a dict's keys must be quoted labels")
            return _finite({label: ev.row(v) for label, v in zip(labels, body.values, strict=True)})
        return _finite(ev.scalar(body))
    except ZeroDivisionError as err:
        raise ToolError("division by zero") from err
    except OverflowError as err:
        raise ToolError("the result overflows a float") from err
    except (ValueError, TypeError) as err:  # math domain errors, wrong argument counts
        raise ToolError(f"cannot evaluate: {err}") from err
    except RecursionError as err:  # a long chain of operators
        raise ToolError("the expression is nested too deeply") from err


class CalcRequest(BaseModel):
    """Input for the exact arithmetic tool."""

    expression: str = Field(
        ..., min_length=1, description="Expression or [list of expressions], e.g. '(37-13)/13*100'."
    )
    values: dict[str, int | float | list[int | float] | str] | None = Field(
        None,
        description="Named inputs: numbers, series (prev: works) or expressions over earlier "
        "values.",
    )

    @field_validator("values", mode="before")
    @classmethod
    def _no_flags(cls, v: Any) -> Any:
        # before coercion: validation would quietly read true/false as 1/0
        for name, value in v.items() if isinstance(v, dict) else ():
            items = value if isinstance(value, list) else [value]
            if any(isinstance(x, bool) for x in items):
                raise ValueError(f"'{name}' is a true/false flag, not a number")
        return v

    @field_validator("values")
    @classmethod
    def _names(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        for name, value in (v or {}).items():
            if not name.isidentifier() or name in _RESERVED:
                raise ValueError(
                    f"'{name}' cannot be a value name (not an identifier, or a function or "
                    "constant): use names like ocf_fy26, or label results with a dict "
                    "expression"
                )
            if isinstance(value, list) and len(value) > MAX_SERIES_LEN:
                raise ValueError(f"series '{name}' has more than {MAX_SERIES_LEN} values")
        return v


_ShownRow = list[Exact] | Exact
_Shown = dict[str, _ShownRow] | list[_ShownRow] | Exact


class CalcOutput(Output):
    """The expression as evaluated and its result: a number, a [list] (rows may be lists) or
    a {label: value} map, as the expression is shaped. ``inputs`` holds the named numbers and
    expressions as given (an embedded ``prev:`` reference appears under its lifted name);
    ``series`` the length of each named series."""

    expression: Code
    result: Annotated[_Shown, hint(rows=_MAX_ITEMS)]  # every value asked for is shown
    inputs: dict[str, Exact | Code] = {}
    series: dict[str, Count] = {}


async def _handler(req: CalcRequest) -> Result:
    values = req.values or {}
    return Result(
        CalcOutput(
            expression=req.expression,
            result=safe_eval(req.expression, req.values),
            inputs={k: v for k, v in values.items() if not isinstance(v, list)},
            series={k: len(v) for k, v in values.items() if isinstance(v, list)},
        )
    )


SPEC = ToolSpec(
    name="calc",
    display_name="core.calc",
    result_kind="compute",
    tier="core",  # always visible: arithmetic comes up in every mode
    description=_DESCRIPTION,
    request_schema=CalcRequest,
    handler=_handler,
    output=CalcOutput,
    tags=("compute", "arithmetic"),
    inline_refs=("expression", "values"),
)
