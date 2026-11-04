# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.valuation.dcf: discounted cash flow with two terminal methods.

Pure-math intrinsic value from an explicit FCF schedule + discount rate +
terminal method. Returns EV, equity value (net of debt), intrinsic value/share,
and the full PV breakdown. Dollars in, dollars out: the caller chooses WACC
(for FCFF, net_debt matters) or cost of equity (FCFE, net_debt=0); the tool
discounts whatever schedule it's given. ``fcf_schedule`` is chain-aware.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Money, Multiple, Number, Price

_DESCRIPTION = (
    "Discounted-cash-flow valuation. Takes an explicit-period FCF schedule "
    "(year 1 to N; accepts a prev: chain reference), a discount_rate (decimal), a "
    "terminal_method (gordon_growth needs terminal_growth_rate; exit_multiple "
    "needs terminal_ebitda + terminal_ebitda_multiple), and optional net_debt + "
    "shares_outstanding. Returns enterprise value, equity value, intrinsic value "
    "per share, and the PV breakdown. Pure-math; give FCF, net debt and shares on one scale "
    "(all in millions, say), since per-share value is equity value ÷ shares."
)


class DCFRequest(BaseModel):
    """Input for a discounted-cash-flow valuation."""

    fcf_schedule: list[float] = Field(
        ...,
        min_length=1,
        max_length=100,  # a rail for long-life/project-finance assets
        description="Explicit-period free cash flows as a flat list, year 1 to year N "
        "(USD, on the scale of net_debt and shares_outstanding), e.g. millions: "
        "[1200, 1320, 1450].",
    )
    discount_rate: float = Field(
        ...,
        gt=0,
        lt=1,
        description="Discount rate, decimal (0.10=10%). WACC for FCFF; cost of equity for FCFE.",
    )
    terminal_method: Literal["gordon_growth", "exit_multiple"] = Field(
        "gordon_growth",
        description="gordon_growth = FCF*(1+g)/(r−g); exit_multiple = year-N EBITDA × multiple.",
    )
    terminal_growth_rate: float | None = Field(
        None,
        ge=-0.1,
        lt=1,
        description="Perpetuity growth g, decimal. Required for gordon_growth; must be < discount_rate.",  # noqa: E501
    )
    terminal_ebitda: float | None = Field(
        None, ge=0, description="Year-N EBITDA for the exit-multiple terminal value."
    )
    terminal_ebitda_multiple: float | None = Field(
        None, gt=0, description="EV/EBITDA multiple at the exit horizon."
    )
    net_debt: float = Field(
        0.0, description="Net debt (debt − cash) subtracted from EV for equity value. 0 for FCFE."
    )
    shares_outstanding: float | None = Field(
        None,
        gt=0,
        description="Diluted shares for per-share value, on the scale of the FCF: with FCF in "
        "millions, shares in millions (335.6). Omit for aggregate only.",
    )

    @model_validator(mode="after")
    def _check_terminal(self) -> Self:
        if self.terminal_method == "gordon_growth":
            if self.terminal_growth_rate is None:
                raise ValueError("terminal_growth_rate is required for gordon_growth.")
            if self.terminal_growth_rate >= self.discount_rate:
                raise ValueError(
                    f"terminal_growth_rate ({self.terminal_growth_rate}) must be < "
                    f"discount_rate ({self.discount_rate}); the perpetuity is undefined otherwise."
                )
        elif self.terminal_ebitda is None or self.terminal_ebitda_multiple is None:
            raise ValueError(
                "terminal_ebitda and terminal_ebitda_multiple are both required for exit_multiple."
            )
        return self


class TerminalInputs(BaseModel):
    method: Code
    terminal_growth_rate: Fraction = None
    fcf_n_plus_1: Money = None
    terminal_ebitda: Money = None
    terminal_ebitda_multiple: Multiple = None


class DCFOutput(Output):
    enterprise_value: Money
    equity_value: Money
    intrinsic_per_share: Price
    tv_share_of_ev: Fraction
    sum_pv_explicit: Money
    terminal_value: Money
    pv_terminal: Money
    terminal_inputs: TerminalInputs
    discount_rate: Fraction
    net_debt: Money
    shares_outstanding: Count
    years: list[Number]
    fcf_schedule: list[Money]
    discount_factors: list[Number]
    pv_fcfs: list[Money]


def compute_dcf(req: DCFRequest) -> dict[str, Any]:
    """Pure-math core (validation already happened at the Pydantic boundary)."""
    r = req.discount_rate
    years = list(range(1, len(req.fcf_schedule) + 1))
    discount_factors = [(1 + r) ** t for t in years]
    pv_fcfs = [fcf / df for fcf, df in zip(req.fcf_schedule, discount_factors, strict=True)]
    sum_pv_explicit = sum(pv_fcfs)

    if req.terminal_method == "gordon_growth":
        g = req.terminal_growth_rate
        fcf_n_plus_1 = req.fcf_schedule[-1] * (1 + g)
        terminal_value = fcf_n_plus_1 / (r - g)
        terminal_inputs = {
            "method": "gordon_growth",
            "terminal_growth_rate": g,
            "fcf_n_plus_1": fcf_n_plus_1,
        }
    else:
        terminal_value = req.terminal_ebitda * req.terminal_ebitda_multiple
        terminal_inputs = {
            "method": "exit_multiple",
            "terminal_ebitda": req.terminal_ebitda,
            "terminal_ebitda_multiple": req.terminal_ebitda_multiple,
        }

    pv_terminal = terminal_value / discount_factors[-1]
    enterprise_value = sum_pv_explicit + pv_terminal
    equity_value = enterprise_value - req.net_debt
    intrinsic_per_share = equity_value / req.shares_outstanding if req.shares_outstanding else None
    tv_share_of_ev = pv_terminal / enterprise_value if enterprise_value else None

    return {
        "years": years,
        "fcf_schedule": list(req.fcf_schedule),
        "discount_factors": discount_factors,
        "pv_fcfs": pv_fcfs,
        "sum_pv_explicit": sum_pv_explicit,
        "terminal_inputs": terminal_inputs,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "intrinsic_per_share": intrinsic_per_share,
        "tv_share_of_ev": tv_share_of_ev,
        "discount_rate": r,
        "net_debt": req.net_debt,
        "shares_outstanding": req.shares_outstanding,
    }


async def _handler(req: DCFRequest) -> Result:
    return Result(DCFOutput(**compute_dcf(req)), Provenance(method=req.terminal_method))


SPEC = ToolSpec(
    name="compute_valuation_dcf",
    display_name="compute.valuation.dcf",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DCFRequest,
    handler=_handler,
    output=DCFOutput,
    tags=("compute", "valuation", "dcf"),
)
