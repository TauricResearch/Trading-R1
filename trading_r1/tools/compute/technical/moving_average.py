# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.technical.moving_average: a pair of SMAs, the price against each, and their cross.

Pure-math trend tool for any window pair (canonical 50/200; 20/50 for a shorter horizon).
Reports both SMAs, the spread, the price's side of each, which average is on top, and the most
recent crossover with how many bars ago it happened (golden = short crosses above long, death =
below: the named states, not a trade call). SMA and crossover arithmetic are shared
with data.market.prices (`technical.indicators`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Price
from trading_r1.tools.compute.series import Prices
from trading_r1.tools.compute.technical.indicators import Cross, last_cross, sma_last

_DESCRIPTION = (
    "Two simple moving averages over a price series (data.market.prices `closes`): both SMAs, "
    "their spread, the last price against each, which is on "
    "top, and the most recent golden/death cross with how many bars ago. Windows default to "
    "50/200; pass e.g. 20/50 for a shorter horizon. Computed on the series passed (the price "
    "tool's own SMA20/50/200 use quoted closes)."
)


class MovingAverageRequest(BaseModel):
    """Input for an SMA pair over a price series."""

    closes: Prices = Field(
        ..., min_length=3, description="Prices oldest to newest (needs at least long_window)."
    )
    short_window: int = Field(50, ge=2, le=1000, description="Short SMA window (canonical 50).")
    long_window: int = Field(200, ge=3, le=2000, description="Long SMA window (canonical 200).")

    @model_validator(mode="after")
    def _check(self) -> MovingAverageRequest:
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be less than long_window")
        if len(self.closes) < self.long_window:
            raise ValueError(f"need at least long_window = {self.long_window} prices")
        return self


class MovingAverageOutput(Output):
    price: Price
    short_window: Count
    long_window: Count
    sma_short: Price
    sma_long: Price
    spread: Price
    spread_pct: Fraction
    price_vs_short: Code
    price_vs_long: Code
    short_above_long: bool
    last_cross: Cross | None = None
    n_bars: Count


def _side(price: float, level: float) -> str:
    return "above" if price > level else "below" if price < level else "at"


def compute_moving_average(req: MovingAverageRequest) -> dict[str, Any]:
    price = req.closes[-1]
    short = sma_last(req.closes, req.short_window)
    long = sma_last(req.closes, req.long_window)
    return {
        "price": price,
        "short_window": req.short_window,
        "long_window": req.long_window,
        "sma_short": short,
        "sma_long": long,
        "spread": short - long,
        "spread_pct": (short - long) / long,
        "price_vs_short": _side(price, short),
        "price_vs_long": _side(price, long),
        "short_above_long": short > long,
        "last_cross": last_cross(req.closes, req.short_window, req.long_window),
        "n_bars": len(req.closes),
    }


async def _handler(req: MovingAverageRequest) -> Result:
    method = f"SMA({req.short_window}) vs SMA({req.long_window})"
    return Result(MovingAverageOutput(**compute_moving_average(req)), Provenance(method=method))


SPEC = ToolSpec(
    name="compute_technical_moving_average",
    display_name="compute.technical.moving_average",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MovingAverageRequest,
    handler=_handler,
    output=MovingAverageOutput,
    tags=("compute", "technical", "sma", "trend", "crossover"),
)
