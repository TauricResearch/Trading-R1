# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.technical.rsi: Wilder's Relative Strength Index.

Pure-math momentum oscillator. Wilder smoothing (the canonical RSI): the first average gain /
loss is the simple mean of the first ``window`` changes, then
``avg_t = ((N-1)*avg_{t-1} + x_t) / N``; ``RSI = 100 − 100 / (1 + avg_gain / avg_loss)``.
Boundaries: no losses gives 100, no gains 0, no movement 50. The 70/30 overbought/oversold
zones are Wilder's standard ones (exposed as parameters); the model owns interpretation.
Source: J. Welles Wilder, *New Concepts in Technical Trading Systems* (1978).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Number
from trading_r1.tools.compute.series import Prices

_DESCRIPTION = (
    "Wilder's RSI momentum oscillator over a price series (data.market.prices `closes`). "
    "Returns the current RSI, its zone against the canonical 70/30 "
    "overbought/oversold thresholds, the RSI series, and how many periods sat in each zone. "
    "window default 14."
)


class RsiRequest(BaseModel):
    """Input for Wilder's RSI over a price series."""

    closes: Prices = Field(
        ..., min_length=3, description="Prices oldest to newest (needs window + 1 values)."
    )
    window: int = Field(14, ge=2, le=200, description="Wilder smoothing window (canonical 14).")
    overbought: float = Field(70.0, gt=50, lt=100, description="Overbought zone (canonical 70).")
    oversold: float = Field(30.0, gt=0, lt=50, description="Oversold zone (canonical 30).")

    @model_validator(mode="after")
    def _enough(self) -> RsiRequest:
        if len(self.closes) < self.window + 1:
            raise ValueError(f"need at least window + 1 = {self.window + 1} prices")
        return self


class RsiOutput(Output):
    rsi: Number
    zone: Code
    period: Count
    overbought: Number
    oversold: Number
    periods_overbought: Count
    periods_oversold: Count
    min: Number
    max: Number
    rsi_series: Annotated[list[Number], hint(hidden=True)]
    n_obs: Count


def _rsi(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def rsi_series(closes: list[float], period: int) -> list[float]:
    """RSI values aligned to ``closes[period:]``."""
    changes = [b - a for a, b in zip(closes, closes[1:], strict=False)]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = [_rsi(avg_gain, avg_loss)]
    for g, lo in zip(gains[period:], losses[period:], strict=True):
        avg_gain = ((period - 1) * avg_gain + g) / period
        avg_loss = ((period - 1) * avg_loss + lo) / period
        out.append(_rsi(avg_gain, avg_loss))
    return out


def compute_rsi(req: RsiRequest) -> dict[str, Any]:
    series = rsi_series(req.closes, req.window)
    current = series[-1]
    zone = (
        "overbought"
        if current >= req.overbought
        else "oversold"
        if current <= req.oversold
        else "neutral"
    )
    return {
        "rsi": current,
        "zone": zone,
        "period": req.window,
        "overbought": req.overbought,
        "oversold": req.oversold,
        "periods_overbought": sum(v >= req.overbought for v in series),
        "periods_oversold": sum(v <= req.oversold for v in series),
        "min": min(series),
        "max": max(series),
        "rsi_series": series,
        "n_obs": len(series),
    }


async def _handler(req: RsiRequest) -> Result:
    return Result(RsiOutput(**compute_rsi(req)), Provenance(method=f"Wilder RSI({req.window})"))


SPEC = ToolSpec(
    name="compute_technical_rsi",
    display_name="compute.technical.rsi",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=RsiRequest,
    handler=_handler,
    output=RsiOutput,
    tags=("compute", "technical", "rsi", "momentum", "oscillator"),
)
