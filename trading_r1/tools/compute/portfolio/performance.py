# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.portfolio.performance: risk-adjusted performance from a return series.

Pure-math. From a series of periodic returns (and an optional benchmark), returns the
standard performance report: annualized return/vol, Sharpe, Sortino, Calmar, max drawdown,
and, when a benchmark is given, beta, Jensen's alpha, tracking error, information ratio,
Treynor, and up/down capture. It also reports the t-stat of the track record
(t = Sharpe x sqrt(years)) and how many years it would take to reach significance.

Annualization uses the sqrt(m) rule and assumes i.i.d. returns.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, rendered
from trading_r1.contract.units import Count, Fraction, Number
from trading_r1.tools.compute.risk.drawdown import max_drawdown, wealth_index
from trading_r1.tools.compute.series import PeriodsPerYear, Returns

_DESCRIPTION = (
    "Risk-adjusted performance from a periodic return series (decimals, e.g. [0.02,-0.01]). "
    "Takes returns and periods_per_year (required: 252 daily, 52 weekly, 12 monthly) from "
    "data.market.prices (`simple_returns`, `periods_per_year`); risk_free_annual from "
    "data.market.rates (`t_bill_rate`, the 3-month bill; default 0); "
    "optional benchmark returns of the same length (an index symbol's). "
    "Returns cumulative return, annualized return/vol, Sharpe, "
    "Sortino, Calmar, max drawdown, and (with a benchmark) beta, alpha, tracking error, "
    "information ratio, Treynor and up/down capture, plus the track-record t-stat. Annualized "
    "return and Calmar are withheld (null) for records under a year, per GIPS."
)
_MIN_POINTS = 3
# GIPS: returns for periods of less than one year must not be annualized.
_MIN_YEARS_TO_ANNUALIZE = 1.0


def performance(
    returns: list[float],
    *,
    periods_per_year: float = 12,
    risk_free_annual: float = 0.0,
    benchmark: list[float] | None = None,
) -> dict[str, Any]:
    """The standard risk-adjusted performance report for a return series."""
    m = periods_per_year
    n = len(returns)
    years = n / m
    rf = risk_free_annual / m  # per-period risk-free (linear approximation)
    root_m = math.sqrt(m)

    mean = statistics.fmean(returns)
    vol = statistics.stdev(returns)  # sample stdev
    log_growth = sum(math.log1p(r) for r in returns)
    cumulative = _expm1(log_growth)
    ann_return = _expm1(log_growth * m / n) if years >= _MIN_YEARS_TO_ANNUALIZE else None
    ann_vol = vol * root_m

    sharpe = (mean - rf) / vol * root_m if vol > 0 else None
    downside = math.sqrt(sum(min(r - rf, 0.0) ** 2 for r in returns) / n)
    sortino = (mean - rf) / downside * root_m if downside > 0 else None
    mdd = max_drawdown(wealth_index(returns))
    calmar = ann_return / abs(mdd) if ann_return is not None and mdd < 0 else None

    # Track-record significance: t = Sharpe_ann x sqrt(years).
    # Valid for a negative Sharpe too: a sufficiently bad record is significantly bad.
    t_stat = sharpe * math.sqrt(years) if sharpe is not None else None
    years_to_sig = (2.0 / abs(sharpe)) ** 2 if sharpe else None

    out: dict[str, Any] = {
        "n_obs": n,
        "periods_per_year": m,
        "years": years,
        "mean_return": mean,
        "volatility": vol,
        "cumulative_return": cumulative,
        "annualized_return": ann_return,
        "annualized_volatility": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": mdd,
        "downside_deviation": downside,
        "t_stat": t_stat,
        "years_to_significance": years_to_sig,
    }

    if benchmark is not None:
        b_mean = statistics.fmean(benchmark)
        b_var = statistics.pvariance(benchmark, b_mean)
        cov = sum((r - mean) * (bb - b_mean) for r, bb in zip(returns, benchmark, strict=True)) / n
        beta = cov / b_var if b_var > 0 else None
        active = [r - bb for r, bb in zip(returns, benchmark, strict=True)]
        te = statistics.stdev(active) if n >= 2 else 0.0
        ir = (mean - b_mean) / te * root_m if te > 0 else None
        # The same check applies to the IR: t_IR = IR_annual * sqrt(years).
        ir_t = ir * math.sqrt(years) if ir is not None else None
        alpha_period = (mean - rf) - (beta * (b_mean - rf) if beta is not None else 0.0)
        treynor = (mean - rf) * m / beta if beta not in (None, 0) else None
        up = [(r, bb) for r, bb in zip(returns, benchmark, strict=True) if bb > 0]
        dn = [(r, bb) for r, bb in zip(returns, benchmark, strict=True) if bb < 0]
        up_cap = (
            statistics.fmean([r for r, _ in up]) / statistics.fmean([bb for _, bb in up])
            if up
            else None
        )
        dn_cap = (
            statistics.fmean([r for r, _ in dn]) / statistics.fmean([bb for _, bb in dn])
            if dn
            else None
        )
        out.update(
            {
                "beta": beta,
                "jensen_alpha_annual": alpha_period * m,
                "tracking_error": te * root_m,
                "information_ratio": ir,
                "information_ratio_t": ir_t,
                "treynor": treynor,
                "up_capture": up_cap,
                "down_capture": dn_cap,
            }
        )
    return out


class PerformanceRequest(BaseModel):
    """Input for a risk-adjusted performance report over a return series."""

    model_config = ConfigDict(allow_inf_nan=False)  # reject NaN/inf inputs

    returns: Returns = Field(
        ..., min_length=_MIN_POINTS, description="Periodic returns, decimals (e.g. [0.02,-0.01])."
    )
    periods_per_year: PeriodsPerYear = Field(
        ...,
        description="Bars per year of the returns: 252 daily, 52 weekly, 12 monthly, 4 quarterly.",
    )
    risk_free_annual: float = Field(0.0, ge=-0.1, le=1, description="Annual risk-free, decimal.")
    benchmark: Returns | None = Field(
        None, description="Benchmark returns, same length as returns (enables IR, beta, capture)."
    )

    @model_validator(mode="after")
    def _benchmark_length(self) -> PerformanceRequest:
        if self.benchmark is not None and len(self.benchmark) != len(self.returns):
            raise ValueError("benchmark must be the same length as returns")
        return self


def _expm1(x: float) -> float | None:
    """e^x − 1, or None where it overflows a float (a compounded growth beyond ~1e308)."""
    try:
        return math.expm1(x)
    except OverflowError:
        return None


class PerformanceOutput(Output):
    model_config = rendered(
        then="information_ratio measures a record against its benchmark, sharpe against cash. "
        "|t_stat| >= 2 "
        "marks a record that tells skill from luck; years_to_significance is how long one this "
        "good needs. Annualization is sqrt(m), i.i.d. returns."
    )

    n_obs: Count
    periods_per_year: Number
    years: Number
    mean_return: Fraction
    volatility: Fraction
    cumulative_return: Fraction
    annualized_return: Fraction = None  # withheld under a year (GIPS)
    annualized_volatility: Fraction
    sharpe: Number
    sortino: Number
    calmar: Number = None  # needs an annualized return and a drawdown
    max_drawdown: Fraction
    downside_deviation: Fraction
    t_stat: Number
    years_to_significance: Number
    # against a benchmark, when one is given
    beta: Number = None
    jensen_alpha_annual: Fraction = None
    tracking_error: Fraction = None
    information_ratio: Number = None
    information_ratio_t: Number = None
    treynor: Fraction = None
    up_capture: Fraction = None
    down_capture: Fraction = None


async def _handler(req: PerformanceRequest) -> Result:
    r = performance(
        req.returns,
        periods_per_year=req.periods_per_year,
        risk_free_annual=req.risk_free_annual,
        benchmark=req.benchmark,
    )
    return Result(PerformanceOutput(**r))


SPEC = ToolSpec(
    name="compute_portfolio_performance",
    display_name="compute.portfolio.performance",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=PerformanceRequest,
    handler=_handler,
    output=PerformanceOutput,
    tags=("compute", "portfolio", "performance", "sharpe", "information-ratio"),
)
