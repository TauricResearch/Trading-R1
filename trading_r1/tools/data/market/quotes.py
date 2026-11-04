# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.quotes: current quotes for many instruments in one request.

A watchlist or portfolio question ("what moved overnight?") needs the latest price and day change
of several names across asset classes. Tauric serves up to 50 instruments in one request
(`POST /quotes`), so this is one call whatever the list holds, not one per symbol.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Percent, Price, Text
from trading_r1.tools.data.symbols import resolve
from trading_r1.tools.data.tauric import Tauric, provenance

_MAX_SYMBOLS = 50
_DESCRIPTION = (
    "Current quotes for up to 50 instruments in one call: last price, day change (in percent "
    "as published), the previous close, the day's range and volume, the 52-week range, the "
    "market state (regular, pre/post market, closed) and when the price was struck. Takes "
    "tickers, indices (^GSPC), futures (CL=F), FX (EURUSD=X) and crypto (BTC-USD). Use for "
    "a watchlist, a portfolio's holdings or a market overview; for one company's valuation "
    "use data.equity.reference, for history data.market.prices."
)


class MarketQuotesRequest(BaseModel):
    """Input for the batch-quote tool."""

    symbols: list[str] = Field(
        ...,
        min_length=1,
        max_length=_MAX_SYMBOLS,
        description="Symbols to quote, e.g. ['NVDA', 'AAPL', '^GSPC', 'CL=F', 'BTC-USD'].",
    )


class Quote(BaseModel):
    price: Price
    change: Price = None
    change_pct: Percent = None  # the day's change in percent, as published
    previous_close: Price = None
    day_high: Price = None
    day_low: Price = None
    volume: Count = None
    high_52w: Price = None
    low_52w: Price = None
    currency: Code = None
    market_state: Code = None  # regular, pre_market, post_market, closed; none for 24h markets
    as_of: Day = None  # when the price was struck (UTC)
    session_date: Day = None


class MarketQuotesOutput(Output):
    """One quote per symbol, in the order asked; `left_out` names each symbol that has none and
    why."""

    quotes: dict[str, Quote]
    left_out: dict[str, Text] = {}
    notes: list[Text] = []


def _quote(q: dict[str, Any]) -> Quote:
    day = q.get("day") or {}
    span = q.get("range_52w") or {}
    return Quote(
        price=q.get("price"),
        change=q.get("change"),
        change_pct=q.get("change_percent"),
        previous_close=(q.get("previous_close") or {}).get("value"),
        day_high=day.get("high"),
        day_low=day.get("low"),
        volume=day.get("volume"),
        high_52w=span.get("high"),
        low_52w=span.get("low"),
        currency=(q.get("instrument") or {}).get("currency"),
        market_state=q.get("market_state"),
        as_of=q.get("source_time"),
        session_date=q.get("session_date"),
    )


async def _handler(req: MarketQuotesRequest) -> Result:
    asked: dict[str, list[str]] = {}  # Tauric key to the spellings asked
    labels: dict[str, str] = {}
    left_out: dict[str, str] = {}
    notes: list[str] = []
    for symbol in req.symbols:
        try:
            inst = resolve(symbol)
        except ToolError as err:
            left_out[symbol] = str(err)
            continue
        if inst.note and inst.note not in notes:
            notes.append(inst.note)
        if symbol not in asked.setdefault(inst.key, []):
            asked[inst.key].append(symbol)
        labels.setdefault(inst.key, inst.label)
    if not asked:
        raise ToolError("no symbol could be quoted: " + "; ".join(left_out.values()))
    async with Tauric() as api:
        body = await api.post("/quotes", {"instruments": list(asked)})
    data, errors = body.get("quotes") or {}, body.get("errors") or {}
    quotes: dict[str, Quote] = {}
    for key, spellings in asked.items():
        if key in data and data[key].get("price") is not None:
            # two instruments can share a label (a stock and a currency pair): the key tells apart
            label = labels[key] if labels[key] not in quotes else key
            quotes[label] = _quote(data[key])
        else:
            why = (errors.get(key) or {}).get("message") or "no price right now"
            left_out.update({symbol: why for symbol in spellings})
    if not quotes:
        raise ToolError("no quotes available: " + "; ".join(left_out.values()))
    as_of = max((q.session_date for q in quotes.values() if q.session_date), default=None)
    return Result(
        MarketQuotesOutput(quotes=quotes, left_out=left_out, notes=notes + api.limits),
        Provenance(**provenance(subject=", ".join(quotes), view="quotes", as_of=as_of)),
    )


SPEC = ToolSpec(
    name="data_market_quotes",
    display_name="data.market.quotes",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MarketQuotesRequest,
    handler=_handler,
    output=MarketQuotesOutput,
    tags=("market", "quotes", "watchlist", "portfolio", "prices", "data"),
)
