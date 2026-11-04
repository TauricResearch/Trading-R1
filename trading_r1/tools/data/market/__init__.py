# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Market-data tools: the Treasury curve, prices for every asset class, symbol search,
sector performance, date-aligned closes across asset classes, the VIX term structure,
futures positioning, the day's movers, the market digest and news."""

from trading_r1.tools.data.market import (
    aligned_prices,
    digest,
    movers,
    news,
    positioning,
    prices,
    quotes,
    rates,
    sectors,
    symbol_search,
    volatility,
)

MARKET_DATA_SPECS = [
    rates.SPEC,
    prices.SPEC,
    symbol_search.SPEC,
    sectors.SPEC,
    aligned_prices.SPEC,
    volatility.SPEC,
    positioning.SPEC,
    quotes.SPEC,
    movers.SPEC,
    digest.SPEC,
    news.SPEC,
]

__all__ = [
    "MARKET_DATA_SPECS",
    "aligned_prices",
    "digest",
    "movers",
    "news",
    "positioning",
    "prices",
    "quotes",
    "rates",
    "sectors",
    "symbol_search",
    "volatility",
]
