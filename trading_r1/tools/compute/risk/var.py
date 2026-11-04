# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.var: Value-at-Risk and Expected Shortfall of a price series.

Pure-math. From a price series (chain from data.market.prices), at a confidence level over a
horizon: VaR, the loss the worst (1 − confidence) of periods exceed, and Expected Shortfall
(ES, CVaR), the average loss in that tail, always ≥ VaR and the coherent measure (FRTB uses
97.5% ES). Each two ways: historical (empirical tail quantile / tail mean) and parametric
normal (VaR −(μ + zσ), ES −(μ − σ*φ(z)/(1 − confidence))), scaled by √horizon. Reported as
positive % losses.
"""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count, Fraction
from trading_r1.tools.compute.series import Prices, pct_returns, percentile

_DESCRIPTION = (
    "Value-at-Risk and Expected Shortfall for a price series (data.market.prices "
    "`closes`): VaR (the loss the worst tail of periods "
    "exceed) and ES (the average loss in that tail), each historical (empirical) and "
    "parametric (normal), as positive % losses. confidence default 0.95 (FRTB ES uses "
    "0.975). horizon_bars is in bars of the closes: days for daily prices, 15-minute "
    "periods for 15m bars; default 1. One series only: for Sharpe and return statistics use "
    "compute.portfolio.performance, for several assets side by side compute.portfolio.compare."
)
_MIN_POINTS = 5


class VarRequest(BaseModel):
    """Input for value-at-risk over a price series."""

    closes: Prices = Field(
        ...,
        min_length=_MIN_POINTS,
        description="Prices oldest to newest (>=5), e.g. [180, 182, 179, 185, 183].",
    )
    confidence: float = Field(0.95, gt=0.5, lt=1, description="Confidence level, e.g. 0.95.")
    horizon_bars: int = Field(
        1, ge=1, le=2520, description="Horizon in bars of the price series (sqrt-scaled)."
    )  # ceiling is a unit-error rail (~10y)


class VarOutput(Output):
    confidence: Fraction
    horizon_days: Count
    historical_var_pct: Fraction
    parametric_var_pct: Fraction
    historical_es_pct: Fraction
    parametric_es_pct: Fraction
    mean_return: Fraction
    volatility: Fraction
    n_obs: Count


def compute_var(prices: list[float], confidence: float, horizon_days: int) -> dict[str, Any]:
    """Historical and parametric VaR and ES from percentage returns."""

    rets = pct_returns(prices)
    scale = horizon_days**0.5
    tail = 1 - confidence
    q = percentile(sorted(rets), tail)
    breaches = [r for r in rets if r <= q]
    hist = -q * scale
    hist_es = -statistics.fmean(breaches) * scale
    mu = statistics.fmean(rets)
    sigma = statistics.pstdev(rets)
    normal = statistics.NormalDist()
    z = normal.inv_cdf(tail)  # negative
    param = -(mu + z * sigma) * scale
    param_es = -(mu - sigma * normal.pdf(z) / tail) * scale
    return {
        "confidence": confidence,
        "horizon_days": horizon_days,
        "historical_var_pct": max(hist, 0.0),
        "parametric_var_pct": max(param, 0.0),
        "historical_es_pct": max(hist_es, 0.0),
        "parametric_es_pct": max(param_es, 0.0),
        "mean_return": mu,
        "volatility": sigma,
        "n_obs": len(rets),
    }


async def _handler(req: VarRequest) -> Result:
    return Result(VarOutput(**compute_var(req.closes, req.confidence, req.horizon_bars)))


SPEC = ToolSpec(
    name="compute_risk_var",
    display_name="compute.risk.var",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=VarRequest,
    handler=_handler,
    output=VarOutput,
    tags=("compute", "risk", "var", "downside"),
)
