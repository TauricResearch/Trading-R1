# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.technical.bollinger: Bollinger Bands (SMA ± k*σ), %B and bandwidth.

Pure-math volatility envelope. Middle = SMA(window); upper/lower = middle ± k*σ with the
population standard deviation (Bollinger's specification, matching TradingView / TA-Lib).
%B = (price − lower) / (upper − lower) places the last price in the band (0 = lower, 1 = upper;
outside [0, 1] is outside the band) and is None when the bands collapse; bandwidth =
(upper − lower) / middle. Source: John Bollinger, *Bollinger on Bollinger Bands* (2001).
"""

from __future__ import annotations

import statistics
from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Fraction, Number, Price
from trading_r1.tools.compute.series import Prices

_DESCRIPTION = (
    "Bollinger Bands over a price series (data.market.prices `closes`): "
    "the current middle/upper/lower bands, %B (where the last price sits in the band), "
    "bandwidth (band width / middle, a volatility gauge) and its range over the window. "
    "window default 20, num_stdev default 2 (the canonical settings)."
)


class BollingerRequest(BaseModel):
    """Input for Bollinger Bands over a price series."""

    closes: Prices = Field(
        ..., min_length=2, description="Prices oldest to newest (needs at least `window`)."
    )
    window: int = Field(20, ge=2, le=500, description="SMA / σ window (canonical 20).")
    num_stdev: float = Field(2.0, gt=0, le=5, description="Band width in σ (canonical 2).")

    @model_validator(mode="after")
    def _enough(self) -> BollingerRequest:
        if len(self.closes) < self.window:
            raise ValueError(f"need at least window = {self.window} prices")
        return self


class BollingerOutput(Output):
    price: Price
    middle: Price
    upper: Price
    lower: Price
    percent_b: Fraction
    bandwidth: Fraction
    bandwidth_min: Fraction
    bandwidth_max: Fraction
    position: Code
    period: Count
    num_stdev: Number
    middle_series: Annotated[list[Price], hint(hidden=True)]
    upper_series: Annotated[list[Price], hint(hidden=True)]
    lower_series: Annotated[list[Price], hint(hidden=True)]
    n_obs: Count


def compute_bollinger(req: BollingerRequest) -> dict[str, Any]:
    p, k, closes = req.window, req.num_stdev, req.closes
    middle, upper, lower, bandwidth = [], [], [], []
    for i in range(p - 1, len(closes)):
        window = closes[i - p + 1 : i + 1]
        m = statistics.fmean(window)
        s = statistics.pstdev(window, m)
        middle.append(m)
        upper.append(m + k * s)
        lower.append(m - k * s)
        bandwidth.append(2 * k * s / m)
    price, up, lo = closes[-1], upper[-1], lower[-1]
    mid = middle[-1]
    position = (
        "above_upper"
        if price > up
        else "below_lower"
        if price < lo
        else "above_middle"
        if price > mid
        else "below_middle"
        if price < mid
        else "at_middle"
    )
    return {
        "price": price,
        "middle": mid,
        "upper": up,
        "lower": lo,
        "percent_b": (price - lo) / (up - lo) if up != lo else None,
        "bandwidth": bandwidth[-1],
        "bandwidth_min": min(bandwidth),
        "bandwidth_max": max(bandwidth),
        "position": position,
        "period": p,
        "num_stdev": k,
        "middle_series": middle,
        "upper_series": upper,
        "lower_series": lower,
        "n_obs": len(middle),
    }


async def _handler(req: BollingerRequest) -> Result:
    method = f"Bollinger({req.window}, {req.num_stdev:g} sd)"
    return Result(BollingerOutput(**compute_bollinger(req)), Provenance(method=method))


SPEC = ToolSpec(
    name="compute_technical_bollinger",
    display_name="compute.technical.bollinger",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=BollingerRequest,
    handler=_handler,
    output=BollingerOutput,
    tags=("compute", "technical", "bollinger", "volatility", "bands"),
)
