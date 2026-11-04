# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.modeling.sensitivity: a 1-D or 2-D parameter sweep over any compute tool.

Re-runs one pure-math compute tool (DCF, LBO, Black-Scholes, bond, portfolio optimization, …)
across a range of one or two of its inputs and tabulates a single scalar output from each run:
a vector for one sweep, a grid for two (rows = the first), shown as one table in the
target's unit for that output. Each cell goes through the same
dispatcher as any tool call, so `prev:` references in ``base_args`` resolve and a failing cell is
recorded and the sweep goes on. Targets are limited to the ``compute`` family.
It is built per run (it dispatches within that run's registry and session).
"""

from __future__ import annotations

import copy
import time
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec, hint, rendered
from trading_r1.contract.units import Code, Number, Text
from trading_r1.runtime.dispatch import dispatch
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState

NAME = "compute_modeling_sensitivity"
# A sweep is one tool call; past this many seconds the remaining cells are reported as not run.
_TIME_BUDGET_S = 60.0
_DESCRIPTION = (
    "Generic sensitivity table for any compute tool except a DCF (compute.valuation."
    "dcf_sensitivity is the DCF grid): re-run it across one or two of its inputs and "
    "tabulate one output. Give tool_name (a compute_* wire name, e.g. compute_valuation_dcf), "
    "base_args (its normal arguments; prev: references work), 1-2 sweeps (param + values), and "
    "metric_path (a dot path into the tool's structured result, e.g. equity_value or "
    "value_creation_bridge.deleveraging). Returns the vector or grid, its min and max, and any "
    "failed cells. Each cell is a full run of the tool: keep sweeps of slow tools (binomial "
    "trees) small."
)


class Sweep(BaseModel):
    """One swept input."""

    param: str = Field(..., min_length=1, description="The input to vary (a top-level argument).")
    values: list[float] = Field(..., min_length=2, max_length=20, description="Values to try.")

    @field_validator("values")
    @classmethod
    def _distinct(cls, v: list[float]) -> list[float]:
        if len(set(v)) < len(v):
            raise ValueError("each value is tried once: drop the repeats")
        return v


class SensitivityRequest(BaseModel):
    """Input for a sensitivity sweep."""

    tool_name: str = Field(..., description="Wire name of the compute tool to sweep.")
    base_args: dict[str, Any] = Field(..., description="The tool's arguments for the base case.")
    sweeps: list[Sweep] = Field(..., min_length=1, max_length=2)
    metric_path: str = Field(..., min_length=1, description="Dot path to the output to tabulate.")


class Axis(BaseModel):
    param: Code
    values: Annotated[list[Number], hint(hidden=True)]  # the table states them


class Extreme(BaseModel):
    value: Number
    at: dict[str, Number]


class Failure(BaseModel):
    at: dict[str, Number]
    reason: Text


class SensitivityOutput(Output):
    model_config = rendered(index="at")

    tool_name: Code
    metric_path: Code
    rows: Axis
    columns: Axis | None = None  # a two-input sweep
    # the table: the first input's values down the rows, a column per value of the second input
    # (or the one metric), in the unit the target gives the metric
    at: list[Number]
    grid: dict[str, list[Number]]
    units: dict[str, Code]
    values: Annotated[list[Number] | list[list[Number]], hint(hidden=True)]
    min: Extreme | None = None
    max: Extreme | None = None
    failures: list[Failure]


def _unit(schema: dict[str, Any] | None, path: str) -> str | None:
    """The unit the target's output schema gives the value at a dot path."""
    if schema is None:
        return None
    defs, node = schema.get("$defs", {}), schema
    for part in [*path.split("."), None]:
        unit = node.get("unit")  # the outer node's: a unit sits beside a nullable's anyOf
        while "$ref" in node or "anyOf" in node:
            if "$ref" in node:
                node = defs[node["$ref"].rsplit("/", 1)[-1]]
            else:
                node = next((b for b in node["anyOf"] if b.get("type") != "null"), {})
            unit = unit or node.get("unit")
        if part is None:
            return unit
        if part in node.get("properties", {}):
            node = node["properties"][part]
        elif isinstance(node.get("additionalProperties"), dict):
            node = node["additionalProperties"]
        elif "items" in node and part.lstrip("-").isdigit():
            node = node["items"]
        else:
            return None
    return None


def _metric(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif (
            isinstance(data, list)
            and part.lstrip("-").isdigit()
            and -len(data) <= int(part) < len(data)
        ):
            data = data[int(part)]
        else:
            return None
    return data if isinstance(data, int | float) and not isinstance(data, bool) else None


def build_sensitivity_spec(session_state: ToolSessionState, registry: ToolRegistry) -> ToolSpec:
    """Build the per-run sensitivity tool bound to the run's registry and session state."""

    async def handler(req: SensitivityRequest) -> Result:
        target = registry.get(req.tool_name)
        if target is None or target.result_kind != "compute" or target.name == NAME:
            sweepable = sorted(
                s.name
                for s in (*registry.core_tools(), *registry.deferred_tools())
                if s.result_kind == "compute" and s.name != NAME
            )
            raise ToolError(
                f"{req.tool_name!r} is not a sweepable compute tool; choose from {sweepable}"
            )
        inputs = set(target.request_schema.model_fields)
        unknown = [s.param for s in req.sweeps if s.param not in inputs]
        if unknown:
            raise ToolError(f"{target.name} has no input {unknown}; its inputs: {sorted(inputs)}")
        first, second = req.sweeps[0], (req.sweeps[1] if len(req.sweeps) == 2 else None)
        started = time.monotonic()
        grid: list[list[Any]] = []
        failures: list[Failure] = []
        found: list[tuple[float, dict[str, float]]] = []
        for v1 in first.values:
            row = []
            for v2 in second.values if second else [None]:
                cell = {first.param: v1, **({second.param: v2} if second else {})}
                if time.monotonic() - started > _TIME_BUDGET_S:
                    row.append(None)
                    failures.append(
                        Failure(at=cell, reason=f"not run: {_TIME_BUDGET_S:g}s budget spent")
                    )
                    continue
                result = await dispatch(
                    target.name,
                    copy.deepcopy(req.base_args) | cell,
                    registry=registry,
                    session_state=session_state,
                    record=False,  # the sweep's own result is the evidence, not each cell
                )
                value = _metric(result.payload, req.metric_path) if result.ok else None
                row.append(value)
                if value is None:
                    reason = result.text[:200] if not result.ok else "metric not found"
                    failures.append(Failure(at=cell, reason=reason))
                else:
                    found.append((value, cell))
            grid.append(row)
        low = min(found, key=lambda c: c[0], default=None)
        high = max(found, key=lambda c: c[0], default=None)
        keys = [f"{second.param}={v!r}" for v in second.values] if second else [req.metric_path]
        unit = _unit(target.output_schema, req.metric_path)
        out = SensitivityOutput(
            tool_name=target.name,
            metric_path=req.metric_path,
            rows=Axis(param=first.param, values=first.values),
            columns=Axis(param=second.param, values=second.values) if second else None,
            at=first.values,
            grid={k: [row[j] for row in grid] for j, k in enumerate(keys)},
            units=dict.fromkeys([*keys, "min.value", "max.value"], unit) if unit else {},
            values=grid if second else [r[0] for r in grid],
            min=None if low is None else Extreme(value=low[0], at=low[1]),
            max=None if high is None else Extreme(value=high[0], at=high[1]),
            failures=failures,
        )
        return Result(out)

    return ToolSpec(
        name=NAME,
        display_name="compute.modeling.sensitivity",
        result_kind="compute",
        tier="deferred",
        description=_DESCRIPTION,
        request_schema=SensitivityRequest,
        handler=handler,
        output=SensitivityOutput,
        tags=("compute", "modeling", "sensitivity", "scenario"),
    )
