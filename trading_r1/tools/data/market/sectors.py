# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.sectors: the eleven US sectors' performance, through their SPDR ETFs.

The sector-rotation view: each GICS sector's return over a window and its realized volatility,
ranked, using the SPDR Select Sector ETFs (the standard sector proxies, a fixed list)
with the S&P 500 (SPY) alongside. Every ETF is measured over the same window: the
dates they all share, so a sector ETF listed later (XLC 2018, XLRE 2015) does not mix an 8-year
return with 28-year ones. Daily and longer bars carry Tauric's dividend-adjusted closes, so
returns are total returns; intraday returns are price returns. Factual returns only; which
sectors lead or lag is read from the ranking.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Fraction, Text
from trading_r1.tools.compute.risk.volatility import realized_vol
from trading_r1.tools.data.market.aligned_prices import align
from trading_r1.tools.data.pricing import (
    EQUITY,
    INTRADAY,
    PriceWindow,
    annualization,
    fetch_bars,
)
from trading_r1.tools.data.tauric import provenance

# SPDR Select Sector ETFs to GICS sector
SECTOR_ETFS = {
    "XLK": "Information Technology",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}
BENCHMARK = "SPY"  # the S&P 500 the sectors make up

_DESCRIPTION = (
    "Performance of the 11 US GICS sectors through their SPDR Select Sector ETFs (XLK, XLF, "
    "XLV, …) with the S&P 500 (SPY) alongside: each one's return over the window (total "
    "return with dividends on daily and longer bars, price return on intraday bars) measured "
    "over the dates all of them share, so late-listed ETFs (XLC 2018) do not mix windows, and "
    "annualized realized volatility, ranked by return. For today's move use "
    "period '5d' with interval 1d, or an intraday interval; a window of one bar has no return. "
    "For one sector in detail, use data.market.prices on its ETF."
)


class SectorsRequest(PriceWindow):
    """Input for the sector performance table."""


class Sector(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    etf: Code
    sector: Code
    return_: Fraction = Field(alias="return")  # total or price, as `basis` says
    volatility: Fraction  # annualized, realized


class SectorsOutput(Output):
    """The sectors ranked by return over the dates all of them share, the S&P 500 beside."""

    start: Day
    end: Day
    n_bars: Count
    basis: Code  # total return (dividends in) on daily bars, price return intraday
    sectors: list[Sector]
    benchmark: Sector
    failed: dict[str, Text] = {}
    notes: list[Text] = []


def sector_rows(
    bars: dict[str, list[dict[str, Any]]], interval: str
) -> tuple[list[str], list[dict[str, Any]]]:
    """Each ETF's return and volatility over the dates all of them share."""
    closes = {sym: [r["close"] for r in rows] for sym, rows in bars.items()}
    dates, prices = align(bars, closes, interval in INTRADAY)
    per_year, _ = annualization(EQUITY, interval, dates)
    rows = [
        {
            "etf": sym,
            "sector": SECTOR_ETFS.get(sym, "S&P 500"),
            "return": p[-1] / p[0] - 1,
            "volatility": realized_vol(p, per_year)[1],
        }
        for sym, p in prices.items()
    ]
    return dates, rows


async def _handler(req: SectorsRequest) -> Result:
    symbols = (*SECTOR_ETFS, BENCHMARK)
    results = await asyncio.gather(
        *(fetch_bars(sym, req.period, req.interval, req.start, req.end) for sym in symbols),
        return_exceptions=True,
    )
    failed = {s: str(r) for s, r in zip(symbols, results, strict=True) if isinstance(r, Exception)}
    bars = {s: r.rows for s, r in zip(symbols, results, strict=True) if s not in failed}
    if BENCHMARK not in bars or len(bars) < 2:
        raise ToolError("sector data unavailable: " + "; ".join(failed.values()))
    dates, rows = sector_rows(bars, req.interval)
    if len(dates) < 2:
        raise ToolError(
            f"a {req.period} window of {req.interval} bars has one bar, so no return; use "
            "period '5d' for the latest daily moves, or an intraday interval for today's"
        )
    benchmark = next(r for r in rows if r["etf"] == BENCHMARK)
    sectors = sorted(
        (r for r in rows if r["etf"] != BENCHMARK), key=lambda r: r["return"], reverse=True
    )
    out = SectorsOutput(
        start=dates[0],
        end=dates[-1],
        n_bars=len(dates),
        basis="price" if req.interval in INTRADAY else "total",
        sectors=sectors,
        benchmark=benchmark,
        failed=failed,
        notes=list(
            dict.fromkeys(n for r in results if not isinstance(r, Exception) for n in r.limits)
        ),
    )
    return Result(out, Provenance(**provenance(subject="US sectors", as_of=dates[-1])))


SPEC = ToolSpec(
    name="data_market_sectors",
    display_name="data.market.sectors",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=SectorsRequest,
    handler=_handler,
    output=SectorsOutput,
    tags=("market", "sectors", "rotation", "performance", "data"),
)
