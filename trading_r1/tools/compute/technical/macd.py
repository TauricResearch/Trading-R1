# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.technical.macd: MACD line, signal line and histogram (Appel 12/26/9).

Pure-math trend/momentum tool. MACD = EMA(fast) − EMA(slow); signal = EMA(signal) of MACD;
histogram = MACD − signal. EMAs are SMA-seeded (StockCharts / TA-Lib), so N prices give
N − slow + 1 MACD values and N − slow − signal + 2 signal/histogram values. States are factual
(MACD above / below / at its signal line) and crossovers are the histogram's sign changes;
the model reads the implication. Source: Gerald Appel (MACD, late 1970s; *Technical Analysis:
Power Tools for Active Investors*, 2005); the histogram is Thomas Aspray's (1986).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Price
from trading_r1.tools.compute.series import Prices
from trading_r1.tools.compute.technical.indicators import ema_sma_seeded, last_sign_change

_DESCRIPTION = (
    "MACD (moving average convergence/divergence) over a price series (data.market.prices "
    "`closes`): the MACD line, signal line, histogram, "
    "whether MACD sits above or below its signal, and the most recent signal-line crossover. "
    "Windows default to the canonical 12/26/9."
)


class MacdRequest(BaseModel):
    """Input for MACD over a price series."""

    closes: Prices = Field(
        ..., min_length=3, description="Prices oldest to newest (needs slow + signal − 1)."
    )
    fast_window: int = Field(12, ge=2, le=200, description="Fast EMA window (canonical 12).")
    slow_window: int = Field(26, ge=3, le=400, description="Slow EMA window (canonical 26).")
    signal_window: int = Field(9, ge=2, le=200, description="Signal EMA window (canonical 9).")

    @model_validator(mode="after")
    def _check(self) -> MacdRequest:
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be less than slow_window")
        need = self.slow_window + self.signal_window - 1
        if len(self.closes) < need:
            raise ValueError(f"need at least slow_window + signal_window − 1 = {need} prices")
        return self


class Crossover(BaseModel):
    macd_crossed: Code
    bars_ago: Count


class MacdOutput(Output):
    macd: Price
    signal: Price
    histogram: Price
    position: Code
    last_crossover: Crossover | None = None
    fast_period: Count
    slow_period: Count
    signal_period: Count
    macd_series: Annotated[list[Price], hint(hidden=True)]
    signal_series: Annotated[list[Price], hint(hidden=True)]
    histogram_series: Annotated[list[Price], hint(hidden=True)]
    n_obs: Count


def compute_macd(req: MacdRequest) -> dict[str, Any]:
    fast = ema_sma_seeded(req.closes, req.fast_window)
    slow = ema_sma_seeded(req.closes, req.slow_window)
    offset = req.slow_window - req.fast_window  # fast starts earlier; align to slow
    macd = [fast[offset + i] - s for i, s in enumerate(slow)]
    signal = ema_sma_seeded(macd, req.signal_window)
    head = req.signal_window - 1
    hist = [macd[head + i] - s for i, s in enumerate(signal)]

    cross = last_sign_change(hist)  # the histogram changes sign when MACD crosses the signal
    h = hist[-1]
    return {
        "macd": macd[-1],
        "signal": signal[-1],
        "histogram": h,
        "position": "above_signal" if h > 0 else "below_signal" if h < 0 else "at_signal",
        "last_crossover": (
            None
            if cross is None
            else {
                "macd_crossed": "above_signal" if cross["direction"] == "up" else "below_signal",
                "bars_ago": cross["bars_ago"],
            }
        ),
        "fast_period": req.fast_window,
        "slow_period": req.slow_window,
        "signal_period": req.signal_window,
        "macd_series": macd,
        "signal_series": signal,
        "histogram_series": hist,
        "n_obs": len(hist),
    }


async def _handler(req: MacdRequest) -> Result:
    method = f"MACD({req.fast_window},{req.slow_window},{req.signal_window})"
    return Result(MacdOutput(**compute_macd(req)), Provenance(method=method))


SPEC = ToolSpec(
    name="compute_technical_macd",
    display_name="compute.technical.macd",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MacdRequest,
    handler=_handler,
    output=MacdOutput,
    tags=("compute", "technical", "macd", "trend", "momentum"),
)
