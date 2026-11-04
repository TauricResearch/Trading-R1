# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.valuation.dcf_sensitivity: a DCF value across a 2-D driver grid.

Reuses the DCF core (`compute_dcf`) to value every cell of a discount-rate ×
terminal-driver grid, to show how intrinsic value moves with the two assumptions
and give a bear/base/bull range. Pure-math; each cell is a DCF, invalid cells
(e.g. g ≥ r) are left blank.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Fraction, Number
from trading_r1.tools.compute.valuation.dcf import DCFRequest, compute_dcf

_DESCRIPTION = (
    "DCF sensitivity grid: values intrinsic value across a range of discount rates "
    "(rows) and a terminal driver (columns): terminal_growth_rates for "
    "gordon_growth, or terminal_ebitda_multiples for exit_multiple. Takes the same "
    "FCF schedule / net_debt / shares as the DCF tool plus the two axes, and returns "
    "the value matrix with the bear/base/bull range. Use it to show how the "
    "valuation depends on WACC and terminal assumptions. For any other tool's inputs use "
    "compute.modeling.sensitivity."
)


class DCFSensitivityRequest(BaseModel):
    """Input for a DCF sensitivity matrix over discount and terminal assumptions."""

    fcf_schedule: list[float] = Field(
        ...,
        min_length=1,
        max_length=100,  # a rail for long-life assets
        description="Explicit-period FCFs as a flat list, year 1 to N (USD), "
        "e.g. [1200, 1320, 1450].",
    )
    discount_rates: list[float] = Field(
        ...,
        min_length=1,
        max_length=25,
        description="Discount-rate axis (rows), decimals e.g. [0.09, 0.10, 0.11].",
    )
    terminal_method: Literal["gordon_growth", "exit_multiple"] = "gordon_growth"
    terminal_growth_rates: list[float] | None = Field(
        None,
        max_length=25,
        description="Terminal growth axis (cols) for gordon_growth, decimals "
        "e.g. [0.02, 0.03, 0.04].",
    )
    terminal_ebitda: float | None = Field(
        None, ge=0, description="Year-N EBITDA for exit_multiple cells."
    )
    terminal_ebitda_multiples: list[float] | None = Field(
        None,
        max_length=25,
        description="EV/EBITDA multiple axis (cols) for exit_multiple, e.g. [10, 12, 14].",
    )
    net_debt: float = Field(0.0, description="Net debt subtracted from EV for equity value.")
    shares_outstanding: float | None = Field(
        None,
        gt=0,
        description="Diluted shares for a per-share grid, on the scale of the FCF: with FCF in "
        "millions, shares in millions (335.6). Omit for equity-value grid.",
    )

    @model_validator(mode="after")
    def _check_axes(self) -> Self:
        if self.terminal_method == "gordon_growth":
            if not self.terminal_growth_rates:
                raise ValueError("terminal_growth_rates is required for gordon_growth.")
        elif self.terminal_ebitda is None or not self.terminal_ebitda_multiples:
            raise ValueError(
                "terminal_ebitda and terminal_ebitda_multiples are required for exit_multiple."
            )
        return self


def _col_axis(req: DCFSensitivityRequest) -> list[float]:
    return (
        req.terminal_growth_rates
        if req.terminal_method == "gordon_growth"
        else req.terminal_ebitda_multiples
    ) or []


def _cell_value(req: DCFSensitivityRequest, dr: float, col: float) -> float | None:
    """One DCF at (discount_rate=dr, terminal driver=col). None if the cell is
    not a valid DCF (e.g. g ≥ r): left blank."""
    kw: dict[str, Any] = {
        "fcf_schedule": req.fcf_schedule,
        "discount_rate": dr,
        "terminal_method": req.terminal_method,
        "net_debt": req.net_debt,
        "shares_outstanding": req.shares_outstanding,
    }
    if req.terminal_method == "gordon_growth":
        kw["terminal_growth_rate"] = col
    else:
        kw["terminal_ebitda"] = req.terminal_ebitda
        kw["terminal_ebitda_multiple"] = col
    try:
        res = compute_dcf(DCFRequest(**kw))
    except (ValidationError, ValueError, ZeroDivisionError):
        return None
    return res["intrinsic_per_share"] if req.shares_outstanding else res["equity_value"]


class DCFSensitivityOutput(Output):
    """``grid[i][j]`` is the ``metric`` (value per share, or equity value without shares) at
    ``discount_rates[i]`` and ``terminal_axis[j]`` (a growth rate or an exit multiple, as
    ``terminal_axis_label`` says); a cell that is not a valid DCF (g ≥ r) is null."""

    metric: Code
    terminal_method: Code
    low: Number
    high: Number
    n_valid: Count
    terminal_axis_label: Code
    discount_rates: list[Fraction]
    terminal_axis: list[Number]
    grid: Annotated[list[list[Number]], hint(axes=("discount_rates", "terminal_axis"))]
    # the units the method and metric give the axis and the values
    units: Annotated[dict[str, Code], hint(hidden=True)]


def compute_sensitivity(req: DCFSensitivityRequest) -> dict[str, Any]:
    """Compute a DCF grid for the request's discount-rate and terminal axes."""

    cols = _col_axis(req)
    grid = [[_cell_value(req, dr, c) for c in cols] for dr in req.discount_rates]
    valid = [v for row in grid for v in row if v is not None]
    metric = "intrinsic_per_share" if req.shares_outstanding else "equity_value"
    return {
        "metric": metric,
        "terminal_method": req.terminal_method,
        "discount_rates": req.discount_rates,
        "terminal_axis": cols,
        "terminal_axis_label": (
            "terminal_growth_rate"
            if req.terminal_method == "gordon_growth"
            else "terminal_ebitda_multiple"
        ),
        "grid": grid,
        "low": min(valid) if valid else None,
        "high": max(valid) if valid else None,
        "n_valid": len(valid),
        "units": {
            "terminal_axis": "fraction" if req.terminal_method == "gordon_growth" else "multiple",
            **dict.fromkeys(
                ("grid", "low", "high"), "price" if req.shares_outstanding else "money"
            ),
        },
    }


async def _handler(req: DCFSensitivityRequest) -> Result:
    res = compute_sensitivity(req)
    return Result(DCFSensitivityOutput(**res), Provenance(method=res["metric"]))


SPEC = ToolSpec(
    name="compute_valuation_dcf_sensitivity",
    display_name="compute.valuation.dcf_sensitivity",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DCFSensitivityRequest,
    handler=_handler,
    output=DCFSensitivityOutput,
    tags=("compute", "valuation", "dcf", "sensitivity"),
)
