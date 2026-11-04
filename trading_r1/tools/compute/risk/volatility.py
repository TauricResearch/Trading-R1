# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.volatility: realized and EWMA volatility of a price series.

Pure-math. Realized volatility is the sample standard deviation of log returns, annualized by
√periods_per_year (the same estimator `data.market.prices` reports). The EWMA estimate
(RiskMetrics) weights recent returns more, σ²_t = λ*σ²_{t−1} + (1 − λ)*r²_{t−1}, so it reacts
to a change in regime that the equal-weighted figure averages away; the reported value is the
forecast for the next period after the last return. λ = 0.94 is the RiskMetrics daily
convention (0.97 monthly); half-life = ln 0.5 / ln λ. Mean-zero throughout, seeded with the
mean-zero second moment.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count, Fraction, Number
from trading_r1.tools.compute.series import PeriodsPerYear, Prices, log_returns

_DESCRIPTION = (
    "Volatility of a price series (data.market.prices `closes`, "
    "`periods_per_year`): equal-weighted realized volatility and the EWMA (RiskMetrics) "
    "estimate that weights recent returns more, both annualized, with the EWMA half-life and "
    "the EWMA/realized ratio. ewma_lambda default 0.94 (RiskMetrics daily). Not for "
    "Sharpe or return statistics (compute.portfolio.performance) or a side-by-side of assets "
    "(compute.portfolio.compare)."
)


class VolatilityRequest(BaseModel):
    """Input for realized and EWMA volatility over a price series."""

    closes: Prices = Field(..., min_length=3, description="Prices oldest to newest.")
    periods_per_year: PeriodsPerYear = Field(
        252, description="Bars per year: 252 daily, 52 weekly, 12 monthly."
    )
    ewma_lambda: float = Field(
        0.94, gt=0, lt=1, description="EWMA decay (RiskMetrics: 0.94 daily, 0.97 monthly)."
    )


class VolatilityOutput(Output):
    realized_volatility: Fraction
    ewma_volatility: Fraction
    realized_per_period: Fraction
    ewma_per_period: Fraction
    ewma_over_realized: Number
    ewma_lambda: Number
    ewma_half_life_periods: Number
    periods_per_year: Number
    n_returns: Count


def realized_vol(prices: list[float], periods_per_year: float) -> tuple[float | None, float | None]:
    """(per-period, annualized) sample stdev of log returns; None for fewer than 3 prices (a
    sample standard deviation needs two returns)."""
    if len(prices) < 3:
        return None, None
    rets = log_returns(prices)
    n = len(rets)
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    per = math.sqrt(var)
    return per, per * math.sqrt(periods_per_year)


def ewma_variance(returns: list[float], lam: float) -> list[float]:
    """EWMA variance path (mean-zero), seeded with the mean-zero second moment."""
    var = sum(r * r for r in returns) / len(returns)
    path = []
    for r in returns:
        var = lam * var + (1 - lam) * r * r
        path.append(var)
    return path


def compute_volatility(req: VolatilityRequest) -> dict[str, Any]:
    root = math.sqrt(req.periods_per_year)
    realized_per, realized_ann = realized_vol(req.closes, req.periods_per_year)
    ewma_per = math.sqrt(ewma_variance(log_returns(req.closes), req.ewma_lambda)[-1])
    return {
        "realized_volatility": realized_ann,
        "ewma_volatility": ewma_per * root,
        "realized_per_period": realized_per,
        "ewma_per_period": ewma_per,
        "ewma_over_realized": ewma_per / realized_per if realized_per else None,
        "ewma_lambda": req.ewma_lambda,
        "ewma_half_life_periods": math.log(0.5) / math.log(req.ewma_lambda),
        "periods_per_year": req.periods_per_year,
        "n_returns": len(req.closes) - 1,
    }


async def _handler(req: VolatilityRequest) -> Result:
    return Result(VolatilityOutput(**compute_volatility(req)))


SPEC = ToolSpec(
    name="compute_risk_volatility",
    display_name="compute.risk.volatility",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=VolatilityRequest,
    handler=_handler,
    output=VolatilityOutput,
    tags=("compute", "risk", "volatility", "ewma"),
)
