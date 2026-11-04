# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.prices: OHLCV history and derived technicals for any priced instrument.

A thin tool over the shared pricing engine (`tools/data/pricing.py`), which documents the
chain-ready series and the price bases. The instrument's kind fixes the asset class and with it
the annualization calendar, so the model never picks one.
"""

from __future__ import annotations

from pydantic import Field

from trading_r1.contract.spec import Result, ToolSpec
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.pricing import (
    PriceOutput,
    PriceWindow,
    asset_class_for,
    fetch_bars,
    price_response,
)

_DESCRIPTION = (
    "OHLCV price history plus derived technicals for any priced instrument: stocks, ETFs and "
    "funds (AAPL), indices (^GSPC, ^DJI, ^IXIC, ^RUT, ^VIX), futures as continuous front-month "
    "contracts (CL=F, GC=F, ES=F; the =F is required), currency pairs (EURUSD or EURUSD=X) and "
    "crypto (BTC-USDT; BTC-USD is served as BTC-USDT). Returns SMA(20/50/200) and last cross, "
    "realized volatility, trailing returns (1d, 1w, 1m, 3m, 6m, 1y, each with the date it "
    "starts from), the closing range with dates, the quote `currency`, and chain-ready "
    "`closes`, `simple_returns`, `dates` and `periods_per_year` (e.g. prev:<id>.closes into "
    "correlation, prev:<id>.simple_returns into performance). "
    "A listed security's daily closes are adjusted for splits and dividends (total-return "
    "basis, `period_return.total`; the latest bar is the traded close); every other class "
    "is on the quoted price (`period_return.price`). `period` counts back from today; `end` "
    "reads a past window (period '1mo' with end 2020-03-31 is March 2020) and `start` opens one "
    "on a date, and every figure then describes that window. Intraday 15m and 60m bars are for "
    "listed securities only. History: ten years for listed securities; indices, futures and FX "
    "from 2021-09-27 (a future's returns across a roll include the roll; FX has no daily close, "
    "so its series is the daily open snapshot, open to open, with each day's high and low); "
    "crypto from 2024-07-01. A window before an instrument's history is refused with the date "
    "it begins. Annualized by class: 252 trading days (securities, indices, futures), 260 "
    "weekdays (FX), 365 days (crypto)."
)


class MarketPricesRequest(PriceWindow):
    """Input for the price history of one instrument."""

    symbol: Symbol = Field(
        ...,
        description="Instrument symbol: AAPL, ^GSPC, CL=F, EURUSD, BTC-USDT or a priced series' "
        "key (series:global.fx_index.us_dollar).",
    )


async def _handler(req: MarketPricesRequest) -> Result:
    bars = await fetch_bars(req.symbol, req.period, req.interval, req.start, req.end)
    asset = asset_class_for(bars.instrument.kind)
    return price_response(req.symbol, bars, req.interval, asset)


SPEC = ToolSpec(
    name="data_market_prices",
    display_name="data.market.prices",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MarketPricesRequest,
    handler=_handler,
    output=PriceOutput,
    tags=("market", "prices", "technical", "equity", "fx", "crypto", "futures", "data"),
)
