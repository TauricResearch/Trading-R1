# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.statistics.descriptive: summary statistics & distributional moments.

Pure-math. From a numeric series, central tendency and
dispersion (sample AND population, since the choice changes the number), quartiles/IQR,
and the higher moments (skewness, excess kurtosis) with the standard error that says
whether they mean anything. The output states which variance
convention was used, that higher moments are noisy at small n (skew SE ~ sqrt(6/n)), and
that skewed cross-sections should be read with the median and IQR, not the mean and sigma.

Skewness and excess kurtosis use the (1/n) standardized-moment formulas with the sample s (the usual
convention); other conventions (population s, Excel/CFA sample-adjusted) differ.
"""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec, rendered
from trading_r1.contract.units import Count, Number
from trading_r1.tools.compute.series import percentile

_DESCRIPTION = (
    "Summary statistics for a numeric series: mean, median, sample & population standard "
    "deviation and variance, min/max/range, quartiles (p25/median/p75) and IQR, skewness and "
    "excess kurtosis (with the standard error of skewness), coefficient of variation, and a "
    "robust scale (normalized MAD). Pass any series "
    "(returns, ratios, a peer-set metric)."
)
_MIN_POINTS = 3
_NOTE = (
    "Skewness and excess kurtosis use the 1/n formula with the sample s (other conventions "
    "differ); below about 100 observations they are indicative. Read a skewed series by its "
    "median and IQR, not mean and standard deviation."
)


def describe(data: list[float]) -> dict[str, Any]:
    """Summary statistics + distributional moments for a numeric series."""
    n = len(data)
    s = sorted(data)
    mean = statistics.fmean(data)
    var_pop = statistics.pvariance(data, mean)
    var_samp = statistics.variance(data, mean)
    std_pop = var_pop**0.5
    std_samp = var_samp**0.5
    median = percentile(s, 0.5)
    p25, p75 = percentile(s, 0.25), percentile(s, 0.75)

    # Higher moments: (1/n) standardized moments using the SAMPLE s (the usual convention).
    if std_samp > 0:
        z3 = sum(((x - mean) / std_samp) ** 3 for x in data) / n
        z4 = sum(((x - mean) / std_samp) ** 4 for x in data) / n
        skewness: float | None = z3
        excess_kurtosis: float | None = z4 - 3
    else:
        skewness = excess_kurtosis = None

    mad = statistics.median(abs(x - median) for x in data) * 1.4826  # robust scale estimate
    return {
        "n": n,
        "mean": mean,
        "median": median,
        "std_sample": std_samp,
        "std_population": std_pop,
        "variance_sample": var_samp,
        "variance_population": var_pop,
        "min": s[0],
        "max": s[-1],
        "range": s[-1] - s[0],
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "skewness": skewness,
        "excess_kurtosis": excess_kurtosis,
        "skewness_stderr": (6.0 / n) ** 0.5,  # noise floor for skew
        "coefficient_of_variation": std_samp / mean if mean != 0 else None,
        "mad_normalized": mad,
    }


class DescriptiveRequest(BaseModel):
    """Input for descriptive statistics over a numeric series."""

    model_config = ConfigDict(allow_inf_nan=False)  # reject NaN/inf inputs

    data: list[float] = Field(
        ..., min_length=_MIN_POINTS, description="Numeric series (>=3), e.g. [3.1,-2.4,1.8]."
    )


class DescriptiveOutput(Output):
    model_config = rendered(then=_NOTE)

    n: Count
    mean: Number
    median: Number
    std_sample: Number
    std_population: Number
    variance_sample: Number
    variance_population: Number
    min: Number
    max: Number
    range: Number
    p25: Number
    p75: Number
    iqr: Number
    skewness: Number  # n/a for a constant series
    excess_kurtosis: Number
    skewness_stderr: Number
    coefficient_of_variation: Number
    mad_normalized: Number


async def _handler(req: DescriptiveRequest) -> Result:
    return Result(DescriptiveOutput(**describe(req.data)))


SPEC = ToolSpec(
    name="compute_statistics_descriptive",
    display_name="compute.statistics.descriptive",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DescriptiveRequest,
    handler=_handler,
    output=DescriptiveOutput,
    tags=("compute", "statistics", "descriptive", "moments", "quant"),
)
