# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Shared numeric-series helpers for the compute tools: pure, no network."""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import Field

# Longest accepted series: a unit-error rail, above 60 days of 2m bars
# around the clock (43,200), the longest window an intraday price tool can be asked for.
MAX_SERIES_LEN = 50_000

# A price series for the price-taking tools: finite, strictly positive, oldest to newest.
Prices = Annotated[
    list[Annotated[float, Field(gt=0, allow_inf_nan=False)]],
    Field(max_length=MAX_SERIES_LEN),
]


# A simple-return series: finite and above −100% (a return of −1 wipes out the position).
Returns = Annotated[
    list[Annotated[float, Field(gt=-1, allow_inf_nan=False)]],
    Field(max_length=MAX_SERIES_LEN),
]


# Bars per year, for annualizing: from 1 (annual) to one-minute bars around the clock (crypto),
# so intraday series from the price tools chain in with their own calendar.
MAX_BARS_PER_YEAR = 365 * 24 * 60
PeriodsPerYear = Annotated[float, Field(ge=1, le=MAX_BARS_PER_YEAR)]


def percentile(sorted_xs: list[float], q: float) -> float:
    """Linear-interpolation percentile (``q`` in [0, 1]) over an ALREADY-SORTED list.

    The caller sorts.
    """
    idx = q * (len(sorted_xs) - 1)
    lo = int(idx)
    if lo + 1 >= len(sorted_xs):
        return sorted_xs[-1]
    return sorted_xs[lo] * (1 - (idx - lo)) + sorted_xs[lo + 1] * (idx - lo)


def pct_returns(prices: list[float]) -> list[float]:
    """Simple period-over-period returns of a strictly positive price series."""
    return [b / a - 1.0 for a, b in zip(prices, prices[1:], strict=False)]


def log_returns(prices: list[float]) -> list[float]:
    """Log returns of a strictly positive price series."""
    logs = [math.log(p) for p in prices]
    return [b - a for a, b in zip(logs, logs[1:], strict=False)]


# For the descriptions of tools that pair series (the model sees these, not the docstring).
ALIGNMENT_NOTE = " Pairs series by position, not date: take them from data.market.aligned_prices."


def align(*series: list[float]) -> list[list[float]]:
    """Align return series to their common length by keeping the most recent N of each,
    so paired stats (correlation, beta) compare the same observations."""
    n = min((len(s) for s in series), default=0)
    return [s[len(s) - n :] for s in series]
