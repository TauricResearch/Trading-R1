# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.aligned_prices: several instruments' closes on the dates they all share.

The cross-sectional feeder. The compute tools that pair series (correlation, beta, portfolio
optimization and risk decomposition) pair them by position, so series fetched one by one, with
different listing dates, holidays and calendars (crypto trades weekends; equities don't), can
pair different days. This tool fetches each symbol in its own asset class (each on Tauric's one
price basis for it: dividend-adjusted for listed securities, quoted otherwise) and keeps only the
bars every symbol has, so `series` are equal-length and date-matched by construction.

`periods_per_year` is the joined series' observed density (dates shared per year), capped at
the most restrictive class calendar: a stock joined to crypto keeps only trading days, and
listings on different exchanges also lose each other's holidays. Intraday bars exist for listed
securities only.

Matching dates are not simultaneous closes: a BTC day ends at midnight UTC, and an FX value is
the day's open snapshot. And each series is in its own listing's currency. History is as deep as
the shallowest symbol: ten years for stocks, five for indices and futures, a few months for
crypto.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import (
    Output,
    Provenance,
    Result,
    ToolError,
    ToolSpec,
    hint,
    rendered,
)
from trading_r1.contract.units import Code, Count, Day, Fraction, Number, Price, Text
from trading_r1.tools.compute.series import pct_returns
from trading_r1.tools.data.common import SymbolList
from trading_r1.tools.data.pricing import (
    EQUITY,
    INTRADAY,
    AssetClass,
    PriceWindow,
    annualization,
    asset_class_for,
    fetch_bars,
    utc_minute,
)
from trading_r1.tools.data.tauric import provenance

_MAX_SYMBOLS = 20
_DAYS_PER_YEAR = 365.25
# Shared dates per year is measured only over a span long enough to average out weekends and
# holidays; a shorter window takes the class calendar.
_MIN_DENSITY_SPAN_DAYS = 90

_DESCRIPTION = (
    "Closes for 2-20 instruments of any asset class (stocks, ETFs, indices, FX, crypto, "
    "futures) aligned on the dates they all share: equal-length, date-matched `series` "
    "(symbol to closes, each on its own price basis) with `dates` and `periods_per_year`. "
    "Feed them to the tools that pair series by position: correlation (series=prev:<id>.series), "
    "portfolio optimization and decomposition (prices=prev:<id>.series, periods_per_year="
    "prev:<id>.periods_per_year), beta (closes=prev:<id>.columns[0], benchmark_closes="
    "prev:<id>.columns[1]; `columns` follows the order of `symbols`, which also serves "
    "symbols with dots, like BRK.B). Bars a symbol has that another lacks (weekends for crypto, "
    "holidays, dates before a listing) are dropped and counted; a symbol Tauric cannot serve is "
    "reported and left out. Matching dates are not simultaneous closes (BTC's day ends at "
    "midnight UTC; an FX value is the day's open snapshot), each series is in its own currency, "
    "and history is as deep as the shallowest symbol."
)


class AlignedPricesRequest(PriceWindow):
    """Input for date-aligned closes."""

    symbols: SymbolList = Field(
        ...,
        min_length=2,
        max_length=_MAX_SYMBOLS,
        description="Instrument symbols, any class (AAPL, ^GSPC, CL=F, EURUSD=X, BTC-USD).",
    )


class SymbolStats(BaseModel):
    asset_class: Code
    instrument_type: Code = None
    currency: Code = None
    bars: Count  # the symbol's own bars in the window
    dropped: Count  # of them, the ones another symbol lacks
    period_return: Fraction  # over the shared window, on the symbol's own price basis


class AlignedPricesOutput(Output):
    """Closes on the dates every symbol shares: `series` (symbol to closes), `columns` (the
    same in `symbols` order), `simple_returns` and `dates`, all chain-ready and not shown."""

    model_config = rendered(
        then="Chain the closes as series (symbol to closes), columns[i] (in symbols order), "
        "simple_returns and dates."
    )

    symbols: list[Code]
    n_dates: Count
    start: Day
    end: Day
    periods_per_year: Number
    per_symbol: dict[str, SymbolStats]
    failed: dict[str, Text] = {}  # left out, and why
    notes: list[Text] = []
    dates: Annotated[list[Day], hint(hidden=True)]
    series: Annotated[dict[str, list[Price]], hint(hidden=True)]
    columns: Annotated[list[list[Price]], hint(hidden=True)]
    simple_returns: Annotated[dict[str, list[Fraction]], hint(hidden=True)]


def align(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    closes_by_symbol: dict[str, list[float]],
    intraday: bool,
) -> tuple[list[str], dict[str, list[float]]]:
    """Inner-join on the date (or instant) every symbol has; keys in time order."""
    keyed = {
        sym: dict(
            zip(
                (utc_minute(r["date"]) if intraday else r["date"] for r in rows),
                closes_by_symbol[sym],
                strict=True,
            )
        )
        for sym, rows in bars_by_symbol.items()
    }
    shared = sorted(set.intersection(*(set(k) for k in keyed.values())))
    return shared, {sym: [keyed[sym][k] for k in shared] for sym in keyed}


def calendar(classes: list[AssetClass], interval: str, dates: list[str]) -> float:
    """Bars per year of the joined series: the observed density of shared dates, capped at the
    most restrictive class calendar (intraday: one class only)."""
    if interval in INTRADAY:
        return annualization(classes[0], interval, dates)[0]
    tightest = min(c.bars_per_year(interval) for c in classes)
    if interval != "1d" or len(dates) < 2:
        return tightest

    span = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days
    if span < _MIN_DENSITY_SPAN_DAYS:
        return tightest
    return min(tightest, (len(dates) - 1) * _DAYS_PER_YEAR / span)


async def _handler(req: AlignedPricesRequest) -> Result:
    results = await asyncio.gather(
        *(fetch_bars(sym, req.period, req.interval, req.start, req.end) for sym in req.symbols),
        return_exceptions=True,
    )
    failed = {
        sym: str(r) for sym, r in zip(req.symbols, results, strict=True) if isinstance(r, ToolError)
    }
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, ToolError):
            raise r  # not a data problem: let it surface
    fetched = {sym: r for sym, r in zip(req.symbols, results, strict=True) if sym not in failed}
    if len(fetched) < 2:
        raise ToolError("fewer than two symbols could be fetched: " + "; ".join(failed.values()))
    symbols = list(fetched)
    intraday = req.interval in INTRADAY
    classes = {
        sym: asset_class_for(b.instrument.kind if b.instrument else None) or EQUITY
        for sym, b in fetched.items()
    }
    rows = {sym: b.rows for sym, b in fetched.items()}
    closes = {sym: [r["close"] for r in rows[sym]] for sym in symbols}
    dates, series = align(rows, closes, intraday)
    if len(dates) < 2:
        raise ToolError("the symbols share fewer than two bars in this window: widen the period")
    per_year = calendar(list(classes.values()), req.interval, dates)
    stats = {
        sym: {
            "asset_class": classes[sym].label,
            "instrument_type": fetched[sym].instrument_type,
            "currency": fetched[sym].currency,
            "bars": len(rows[sym]),
            "dropped": len(rows[sym]) - len(dates),
            "period_return": series[sym][-1] / series[sym][0] - 1,
        }
        for sym in symbols
    }
    # a class's basis note (a future's roll, a yield index's units) holds here as in its tool
    notes = dict.fromkeys(c.caveat for c in classes.values() if c.caveat)
    notes.update(
        dict.fromkeys(
            b.instrument.note for b in fetched.values() if b.instrument and b.instrument.note
        )
    )
    notes.update(dict.fromkeys(n for b in fetched.values() for n in b.limits))
    out = AlignedPricesOutput(
        symbols=symbols,
        n_dates=len(dates),
        start=dates[0],
        end=dates[-1],
        periods_per_year=per_year,
        per_symbol=stats,
        failed=failed,
        notes=list(notes),
        dates=dates,
        series=series,
        columns=[series[sym] for sym in symbols],  # positional: for dotted symbols
        simple_returns={sym: pct_returns(v) for sym, v in series.items()},
    )
    return Result(out, Provenance(**provenance(subject=", ".join(symbols), as_of=dates[-1])))


SPEC = ToolSpec(
    name="data_market_aligned_prices",
    display_name="data.market.aligned_prices",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=AlignedPricesRequest,
    handler=_handler,
    output=AlignedPricesOutput,
    tags=("market", "prices", "aligned", "cross-asset", "correlation", "data"),
)
