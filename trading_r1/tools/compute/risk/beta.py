# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.beta: beta of an asset vs a benchmark, from price series.

Pure-math. From an asset price series and a benchmark price series (both from
data.market.prices: the asset symbol and an index symbol like ^GSPC; chain each
`prev:<id>.closes`), converts to returns, aligns, and computes
β = cov(asset, bench) / var(bench), plus its OLS standard error, the Blume-adjusted beta
(2/3*β + 1/3), the correlation and R². A window-explicit beta beside the static one in
data.equity.reference.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Count, Number
from trading_r1.tools.compute.series import ALIGNMENT_NOTE, Prices, align, pct_returns

_DESCRIPTION = (
    "Beta of an asset vs a benchmark from price series (closes + benchmark_closes, "
    "best from one data.market.aligned_prices call on both, e.g. symbols [stock, ^GSPC]: "
    "closes=prev:<id>.columns[0], benchmark_closes=prev:<id>.columns[1]). Converts "
    "to returns, aligns the common window, and returns beta = cov/var(benchmark) with its "
    "standard error, the Blume-adjusted beta (2/3*beta + 1/3, the convention for a CAPM input "
    "when the raw estimate is noisy: a low R^2 or a wide standard error), correlation and R^2. "
    "A window-explicit beta vs the static one in reference." + ALIGNMENT_NOTE
)
_MIN_POINTS = 3
# Blume (1971): betas regress toward the market's 1; adjusted = 2/3*raw + 1/3, the weights
# Bloomberg's "adjusted beta" uses. The CAPM input when the raw estimate is noisy.
_BLUME_RAW_WEIGHT = 2 / 3


class BetaRequest(BaseModel):
    """Input for estimating asset beta versus a benchmark series."""

    closes: Prices = Field(
        ..., min_length=_MIN_POINTS, description="Asset price series, e.g. [180, 182, 179, 185]."
    )
    benchmark_closes: Prices = Field(
        ...,
        min_length=_MIN_POINTS,
        description="Benchmark (e.g. index) price series, e.g. [4500, 4520, 4490, 4550].",
    )
    # length is enforced by Field(min_length=_MIN_POINTS) on both fields.


class BetaOutput(Output):
    beta: Number
    beta_std_error: Number
    adjusted_beta: Number
    correlation: Number
    r_squared: Number
    n_obs: Count


def compute_beta(closes: list[float], benchmark_closes: list[float]) -> dict[str, Any]:
    """Compute beta from aligned asset and benchmark percentage returns."""

    ra, rb = align(pct_returns(closes), pct_returns(benchmark_closes))
    if len(ra) < 2:
        raise ToolError("not enough overlapping observations to estimate beta")
    var_b = statistics.variance(rb)
    beta = statistics.covariance(ra, rb) / var_b if var_b else None
    corr = statistics.correlation(ra, rb) if var_b and statistics.variance(ra) else None
    n = len(ra)
    # OLS slope standard error: sqrt((1 − R²)/(n − 2)) * sd(asset)/sd(benchmark)
    se = (
        math.sqrt((1 - corr**2) / (n - 2)) * statistics.stdev(ra) / statistics.stdev(rb)
        if corr is not None and n > 2
        else None
    )
    adjusted = _BLUME_RAW_WEIGHT * beta + (1 - _BLUME_RAW_WEIGHT) if beta is not None else None
    return {
        "beta": beta,
        "beta_std_error": se,
        "adjusted_beta": adjusted,
        "correlation": corr,
        "r_squared": corr**2 if corr is not None else None,
        "n_obs": n,
    }


async def _handler(req: BetaRequest) -> Result:
    return Result(BetaOutput(**compute_beta(req.closes, req.benchmark_closes)))


SPEC = ToolSpec(
    name="compute_risk_beta",
    display_name="compute.risk.beta",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=BetaRequest,
    handler=_handler,
    output=BetaOutput,
    tags=("compute", "risk", "beta", "capm"),
)
