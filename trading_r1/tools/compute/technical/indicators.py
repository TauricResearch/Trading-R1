# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Shared moving-average primitives for the technical tools and `data.market.prices`.

Pure series math, oldest to newest, one implementation of each primitive. The results depend
on the series passed in: `data.market.prices` computes its own SMAs on quoted closes, while the
technical tools compute on whatever series they are given (usually the adjusted `closes`).
"""

from __future__ import annotations

from pydantic import BaseModel

from trading_r1.contract.units import Code, Count


class Cross(BaseModel):
    """The latest SMA crossover: golden (the short crosses above the long) or death."""

    type: Code
    bars_ago: Count


def sma_last(values: list[float], window: int) -> float | None:
    """Simple moving average of the last ``window`` values, or None if too short."""
    return sum(values[-window:]) / window if len(values) >= window else None


def sma_series(values: list[float], window: int) -> list[float | None]:
    """Rolling SMA aligned to ``values`` (None until ``window`` values are available)."""
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        out.append(running / window if i + 1 >= window else None)
    return out


def ema_sma_seeded(values: list[float], period: int) -> list[float]:
    """EMA seeded with the SMA of the first ``period`` values (the StockCharts / TA-Lib
    convention); length ``len(values) - period + 1``, with α = 2 / (period + 1)."""
    out = [sum(values[:period]) / period]
    alpha = 2.0 / (period + 1.0)
    for v in values[period:]:
        out.append(alpha * v + (1.0 - alpha) * out[-1])
    return out


def last_sign_change(diff: list[float | None]) -> dict | None:
    """The most recent crossing of zero in ``diff``: ``{"direction": "up" | "down",
    "bars_ago": n}``, or None. An up-cross goes from ≤ 0 to > 0; a down-cross from ≥ 0 to < 0.
    None entries (warm-up) are skipped."""
    last: tuple[str, int] | None = None
    for i in range(1, len(diff)):
        prev, cur = diff[i - 1], diff[i]
        if prev is None or cur is None:
            continue
        if prev <= 0 < cur:
            last = ("up", i)
        elif prev >= 0 > cur:
            last = ("down", i)
    return None if last is None else {"direction": last[0], "bars_ago": len(diff) - 1 - last[1]}


def last_cross(values: list[float], short: int = 50, long: int = 200) -> dict | None:
    """The most recent crossover of SMA(short) and SMA(long): ``{"type": "golden" | "death",
    "bars_ago": n}`` (golden = short crosses above long), or None if none in the series."""
    s_short, s_long = sma_series(values, short), sma_series(values, long)
    diff = [None if a is None or b is None else a - b for a, b in zip(s_short, s_long, strict=True)]
    change = last_sign_change(diff)
    if change is None:
        return None
    return {
        "type": "golden" if change["direction"] == "up" else "death",
        "bars_ago": change["bars_ago"],
    }
