# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.statistics.regression: ordinary least squares (single regressor).

Pure-math, dependency-free. Fits y = b0 + b1*x by OLS and returns the full inferential
set: slope and intercept, R^2 and adjusted R^2, standard errors, t-statistics, two-sided
p-values (Student-t via the regularized incomplete beta) and the 95% confidence interval on
the slope. The output states that the slope IS beta and the intercept IS
alpha when y is asset returns and x is market returns (don't call alpha outperformance
unless its t is significant), correlation is not causation, and on financial time series
serial correlation inflates these i.i.d.-OLS t-stats, so they are an optimistic bound.

Single regressor only.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec, rendered
from trading_r1.contract.units import Count, Number

_DESCRIPTION = (
    "Ordinary least squares of y on a single regressor x (same-length numeric series). "
    "Returns slope and intercept, R^2 and adjusted R^2, Pearson correlation, standard errors, "
    "t-stats, two-sided p-values, and the 95% CI on the slope. For a CAPM beta, pass asset "
    "excess returns as y and market excess returns as x: the slope is beta, the intercept alpha."
)
_MIN_POINTS = 3
_NOTE = (
    "With y asset and x market returns, the slope is beta and the intercept alpha: alpha is "
    "outperformance only when its t is significant. Correlation is not causation. Serial "
    "correlation in financial time series inflates these i.i.d. OLS t-statistics: read them as "
    "an optimistic bound (HAC / Newey-West errors for inference)."
)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Numerical Recipes)."""
    fpmin, eps, maxit = 1e-300, 3e-14, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value P(|T| > |t|) for a Student-t with df degrees of freedom."""
    if df <= 0:
        return float("nan")
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def _t_crit_95(df: int) -> float:
    """The two-sided 95% critical value t_{0.975, df} (bisection on the p-value)."""
    lo, hi = 0.0, 1000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _t_two_sided_p(mid, df) > 0.05:
            lo = mid  # not far enough into the tail yet
        else:
            hi = mid
    return (lo + hi) / 2


def ols(x: list[float], y: list[float]) -> dict[str, Any]:
    """Single-regressor OLS with full inference."""
    n = len(x)
    df = n - 2
    xbar, ybar = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((xi - xbar) ** 2 for xi in x)
    sxy = sum((xi - xbar) * (yi - ybar) for xi, yi in zip(x, y, strict=True))
    syy = sum((yi - ybar) ** 2 for yi in y)

    if sxx == 0:  # constant regressor: the slope is unidentified
        raise ToolError("regressor x has zero variance: a regression on a constant x is undefined")

    slope = sxy / sxx
    intercept = ybar - slope * xbar
    sse = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y, strict=True))
    r2 = 1 - sse / syy if syy > 0 else None
    adj_r2 = 1 - (1 - r2) * (n - 1) / df if (r2 is not None and df > 0) else None
    pearson = sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else None

    se_est = (sse / df) ** 0.5 if df > 0 else None
    se_slope = se_est / sxx**0.5 if se_est is not None else None
    se_intercept = se_est * (1.0 / n + xbar * xbar / sxx) ** 0.5 if se_est is not None else None
    t_slope = slope / se_slope if se_slope else None
    t_intercept = intercept / se_intercept if se_intercept else None
    tcrit = _t_crit_95(df) if df > 0 else None
    # A perfect fit (se_slope == 0) has no finite inference: keep t/p/CI all None together.
    ci = (
        [slope - tcrit * se_slope, slope + tcrit * se_slope]
        if (tcrit is not None and se_slope)
        else None
    )
    f_stat = (r2 / (1 - r2)) * df if (r2 is not None and r2 < 1 and df > 0) else None

    def _rp(t: float | None) -> float | None:
        return None if t is None else _t_two_sided_p(t, df)

    return {
        "n": n,
        "df": df,
        "slope": slope,
        "intercept": intercept,
        "r_squared": r2,
        "adjusted_r_squared": adj_r2,
        "pearson_r": pearson,
        "std_err_estimate": se_est,
        "std_err_slope": se_slope,
        "std_err_intercept": se_intercept,
        "t_slope": t_slope,
        "t_intercept": t_intercept,
        "p_value_slope": _rp(t_slope),
        "p_value_intercept": _rp(t_intercept),
        "slope_ci_95": ci,
        "f_stat": f_stat,
    }


class RegressionRequest(BaseModel):
    """Input for a single-regressor OLS (y on x)."""

    model_config = ConfigDict(allow_inf_nan=False)  # reject NaN/inf inputs

    y: list[float] = Field(
        ..., min_length=_MIN_POINTS, description="Dependent series (e.g. asset returns)."
    )
    x: list[float] = Field(
        ..., min_length=_MIN_POINTS, description="Regressor series (e.g. market returns)."
    )

    @model_validator(mode="after")
    def _same_length(self) -> RegressionRequest:
        if len(self.x) != len(self.y):
            raise ValueError("x and y must be the same length")
        return self


class RegressionOutput(Output):
    model_config = rendered(then=_NOTE)

    n: Count
    df: Count
    slope: Number
    intercept: Number
    r_squared: Number
    adjusted_r_squared: Number
    pearson_r: Number
    std_err_estimate: Number
    std_err_slope: Number
    std_err_intercept: Number
    t_slope: Number
    t_intercept: Number
    p_value_slope: Number
    p_value_intercept: Number
    slope_ci_95: list[Number] | None = None  # none for a perfect fit
    f_stat: Number


async def _handler(req: RegressionRequest) -> Result:
    return Result(RegressionOutput(**ols(req.x, req.y)))


SPEC = ToolSpec(
    name="compute_statistics_regression",
    display_name="compute.statistics.regression",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=RegressionRequest,
    handler=_handler,
    output=RegressionOutput,
    tags=("compute", "statistics", "regression", "ols", "beta", "quant"),
)
