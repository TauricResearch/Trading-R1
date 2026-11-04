# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.portfolio.compare: side-by-side risk/return profile of named assets.

Takes the whole named set at once and returns one table keyed by name: each asset's annualized
return and volatility, Sharpe, Sortino, Calmar and max drawdown from its own full history
(compute.portfolio.performance on its simple returns), plus
the factual extremes per metric (highest / lowest; which end is desirable is the reader's
call). Assets are not aligned to a common window, so a longer history is not truncated; align
first (or pass equal-length series) when a same-period comparison is needed.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Number
from trading_r1.tools.compute.portfolio.performance import performance
from trading_r1.tools.compute.series import PeriodsPerYear, Prices, pct_returns

_DESCRIPTION = (
    "Compare named assets' risk/return profiles in one table. Pass prices as a map of name to "
    "price series (`closes` per asset) with periods_per_year and "
    "risk_free_annual (data.market.rates t_bill_rate; Sharpe and Sortino need it). Returns each "
    "asset's cumulative and annualized return (annualized only over a year or more, per GIPS), "
    "volatility, Sharpe, Sortino, Calmar and max drawdown, and "
    "which asset is highest and lowest on each. Each asset is measured over its own history "
    "(observation counts shown); pass equal-length series for a same-period comparison."
)
_METRICS = (
    "cumulative_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe",
    "sortino",
    "calmar",
    "max_drawdown",
)


class CompareRequest(BaseModel):
    """Input for a side-by-side comparison of named assets."""

    model_config = ConfigDict(allow_inf_nan=False)

    prices: Annotated[
        dict[str, Annotated[Prices, Field(min_length=4)]], Field(min_length=2, max_length=50)
    ] = Field(..., description="Map of asset name to price series.")
    periods_per_year: PeriodsPerYear = Field(252, description="Bars per year (252 daily).")
    risk_free_annual: float = Field(0.0, ge=-0.1, lt=1, description="Annual risk-free rate.")


class AssetProfile(BaseModel):
    cumulative_return: Fraction
    annualized_return: Fraction = None  # only over a year or more
    annualized_volatility: Fraction
    sharpe: Number
    sortino: Number
    calmar: Number = None  # needs an annualized return
    max_drawdown: Fraction
    n_obs: Count


class Extremes(BaseModel):
    highest: Code
    lowest: Code


class CompareOutput(Output):
    assets: dict[str, AssetProfile]
    extremes: dict[str, Extremes]
    periods_per_year: Number
    risk_free_annual: Fraction


def compute_compare(req: CompareRequest) -> dict[str, Any]:
    rows = {}
    for name, series in req.prices.items():
        p = performance(
            pct_returns(series),
            periods_per_year=req.periods_per_year,
            risk_free_annual=req.risk_free_annual,
        )
        rows[name] = {key: p[key] for key in _METRICS} | {"n_obs": p["n_obs"]}
    extremes = {}
    for key in _METRICS:
        vals = {n: r[key] for n, r in rows.items() if r[key] is not None}
        if vals:
            extremes[key] = {"highest": max(vals, key=vals.get), "lowest": min(vals, key=vals.get)}
    return {
        "assets": rows,
        "extremes": extremes,
        "periods_per_year": req.periods_per_year,
        "risk_free_annual": req.risk_free_annual,
    }


async def _handler(req: CompareRequest) -> Result:
    return Result(CompareOutput(**compute_compare(req)))


SPEC = ToolSpec(
    name="compute_portfolio_compare",
    display_name="compute.portfolio.compare",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CompareRequest,
    handler=_handler,
    output=CompareOutput,
    tags=("compute", "portfolio", "compare", "risk-return"),
)
