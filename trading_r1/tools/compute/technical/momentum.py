# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.technical.momentum: trailing returns and 12-1 momentum from a price series.

Pure-math momentum measures: trailing returns over 1/3/6/12 months and the 12-1 momentum (the
return from 12 months ago to 1 month ago, skipping the most recent month because short-term
reversal contaminates it: the construction of Carhart's (1997) and Fama-French's UMD factor,
after the momentum effect of Jegadeesh & Titman, 1993). That effect ranks stocks against each
other; this is one series' own measure, the input to such a ranking. Optional relative strength:
the 12-month return minus a benchmark's over the same span. A horizon of m months spans round(m
* periods_per_year / 12) bars (chain `periods_per_year` from data.market.prices). With `dates`,
each horizon reports its start and end date; with `benchmark_dates` too, the two series must end
on the same date. Use the adjusted `closes` so returns include dividends.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count, Day, Fraction
from trading_r1.tools.compute.series import MAX_BARS_PER_YEAR, Prices

_DESCRIPTION = (
    "Momentum from a price series (data.market.prices `closes`, `dates`, "
    "`periods_per_year`): trailing 1/3/6/12-month returns with "
    "each window's dates, and the 12-1 momentum (12 months ago to 1 month ago, the construction "
    "of the momentum factor; needs about 13 months of history, so fetch 2y). Pass "
    "benchmark_closes and benchmark_dates (e.g. from SPY) for 12-month relative strength."
)
_HORIZONS_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "12m": 12}


class MomentumRequest(BaseModel):
    """Input for momentum measures over a price series."""

    closes: Prices = Field(..., min_length=2, description="Prices oldest to newest.")
    # Not PeriodsPerYear: the horizons are months, so bars must be monthly or finer.
    periods_per_year: float = Field(
        252,
        ge=12,
        le=MAX_BARS_PER_YEAR,
        description="Bars per year: 252 daily, 52 weekly, 12 monthly.",
    )
    dates: list[str] | None = Field(None, description="Dates aligned to closes (optional).")
    benchmark_closes: Prices | None = Field(
        None, min_length=2, description="Optional benchmark prices for relative strength."
    )
    benchmark_dates: list[str] | None = Field(
        None, description="Dates aligned to benchmark_closes (optional)."
    )

    @model_validator(mode="after")
    def _aligned(self) -> MomentumRequest:
        if self.dates is not None and len(self.dates) != len(self.closes):
            raise ValueError("dates must be the same length as closes")
        if self.benchmark_dates is not None:
            if self.benchmark_closes is None:
                raise ValueError("benchmark_dates needs benchmark_closes")
            if len(self.benchmark_dates) != len(self.benchmark_closes):
                raise ValueError("benchmark_dates must be the same length as benchmark_closes")
            if self.dates is not None and self.dates[-1] != self.benchmark_dates[-1]:
                raise ValueError(
                    f"series end on different dates ({self.dates[-1]} vs "
                    f"{self.benchmark_dates[-1]}); fetch them over the same period"
                )
        return self


class Window(BaseModel):
    start: Day
    end: Day


class MomentumOutput(Output):
    trailing_returns: dict[str, Fraction]
    windows: dict[str, Window | None]
    momentum_12_1: Fraction
    momentum_12_1_window: Window | None = None
    benchmark_return_12m: Fraction = None
    relative_strength_12m: Fraction = None
    horizon_bars: dict[str, Count]
    n_bars: Count


def _return(closes: list[float], start_back: int, end_back: int = 0) -> float | None:
    """Return from ``start_back`` bars ago to ``end_back`` bars ago; None if out of range."""
    if not 0 <= end_back < start_back < len(closes):
        return None
    return closes[-1 - end_back] / closes[-1 - start_back] - 1.0


def _window(dates: list[str] | None, start_back: int, end_back: int = 0) -> dict | None:
    if dates is None or start_back >= len(dates):
        return None
    return {"start": dates[-1 - start_back], "end": dates[-1 - end_back]}


def compute_momentum(req: MomentumRequest) -> dict[str, Any]:
    bars = {k: max(1, round(m * req.periods_per_year / 12)) for k, m in _HORIZONS_MONTHS.items()}
    trailing = {k: _return(req.closes, b) for k, b in bars.items()}
    bench_12m = _return(req.benchmark_closes, bars["12m"]) if req.benchmark_closes else None
    return {
        "trailing_returns": trailing,
        "windows": {k: _window(req.dates, b) for k, b in bars.items()},
        "momentum_12_1": _return(req.closes, bars["12m"], bars["1m"]),
        "momentum_12_1_window": _window(req.dates, bars["12m"], bars["1m"]),
        "benchmark_return_12m": bench_12m,
        "relative_strength_12m": (
            trailing["12m"] - bench_12m
            if trailing["12m"] is not None and bench_12m is not None
            else None
        ),
        "horizon_bars": bars,
        "n_bars": len(req.closes),
    }


async def _handler(req: MomentumRequest) -> Result:
    return Result(MomentumOutput(**compute_momentum(req)))


SPEC = ToolSpec(
    name="compute_technical_momentum",
    display_name="compute.technical.momentum",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MomentumRequest,
    handler=_handler,
    output=MomentumOutput,
    tags=("compute", "technical", "momentum", "factor", "relative-strength"),
)
