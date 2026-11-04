# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.estimates: the sell-side consensus.

What Tauric publishes of the analyst record: price targets (mean, median, high, low), the
rating spread (strong buy … strong sell) with the number of opinions, trailing and forward EPS,
and the next report's EPS and revenue estimates. Estimate revisions, rating actions by firm and
growth by period are not served. Numbers only: estimates are presented as consensus
expectations, not facts, and no rating label is derived from them.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Fraction, Money, Price, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import snapshot
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Analyst consensus for a public equity: price targets (mean/median/high/low, with the "
    "implied move from the current price), the rating spread (strong buy, buy, hold, sell, "
    "strong sell) with the number of ratings and the vendor's recommendation label, trailing "
    "and forward EPS (and the growth between them), the number of analysts, and the report on "
    "the calendar: its date, whether it is `upcoming` or already past, and its consensus EPS and "
    "revenue estimate. Prices and EPS are in "
    "`currency`, the listing's; the revenue estimate is in the reporting currency. "
    "Forward-looking sell-side estimates: present them as consensus expectations. "
    "The earnings record is data.equity.earnings."
)
_RATINGS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
_TARGETS = {
    "mean": "target_mean_price",
    "median": "target_median_price",
    "high": "target_high_price",
    "low": "target_low_price",
}


class EquityEstimatesRequest(BaseModel):
    """Input for fetching analyst consensus estimates."""

    symbol: Symbol = Field(..., description="Equity ticker (e.g. NVDA).")


class Targets(BaseModel):
    mean: Price = None
    median: Price = None
    high: Price = None
    low: Price = None
    current_price: Price = None
    upside_to_mean: Fraction = None
    upside_to_median: Fraction = None


class Ratings(BaseModel):
    strong_buy: Count = None
    buy: Count = None
    hold: Count = None
    sell: Count = None
    strong_sell: Count = None
    total: Count = None
    recommendation: Code = None


class Eps(BaseModel):
    trailing: Price = None
    forward: Price = None
    forward_growth: Fraction = None


class NextReport(BaseModel):
    """The report on the calendar: next (`upcoming`), or the latest until the next is set."""

    date: Day = None
    upcoming: bool | None = None
    eps_estimate: Price = None
    revenue_estimate: Money = None  # in the reporting currency


class EstimatesOutput(Output):
    ticker: Code
    currency: Code = None
    num_analysts: Count = None
    price_targets: Targets
    ratings: Ratings
    eps: Eps
    next_report: NextReport | None = None
    notes: list[Text] = []


def _consensus(a: dict[str, Any], price: float | None, calendar: dict[str, Any]) -> dict[str, Any]:
    targets = {k: a.get(f) for k, f in _TARGETS.items() if a.get(f) is not None}
    if price:
        targets["current_price"] = price
        targets["upside_to_mean"] = targets["mean"] / price - 1 if "mean" in targets else None
        targets["upside_to_median"] = targets["median"] / price - 1 if "median" in targets else None
    trailing, forward = a.get("trailing_eps"), a.get("forward_eps")
    eps = {"trailing": trailing, "forward": forward}
    if trailing and forward is not None:
        eps["forward_growth"] = (forward - trailing) / abs(trailing)
    earnings = calendar.get("earnings") or {}
    day = earnings.get("earnings_date")
    return {
        "price_targets": {k: v for k, v in targets.items() if v is not None},
        "ratings": {
            k: v
            for k, v in {
                **{k: a.get(k) for k in _RATINGS},
                "total": a.get("ratings_total"),
                "recommendation": a.get("recommendation"),
            }.items()
            if v is not None
        },
        "num_analysts": a.get("number_of_analysts"),
        "eps": {k: v for k, v in eps.items() if v is not None},
        "next_report": {
            k: v
            for k, v in (
                ("date", day),
                ("upcoming", day >= dt.date.today().isoformat() if day else None),
                ("eps_estimate", earnings.get("eps_estimate")),
                ("revenue_estimate", earnings.get("revenue_estimate")),
            )
            if v is not None
        },
    }


async def _handler(req: EquityEstimatesRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        analyst, quote, calendar = await asyncio.gather(
            snapshot.analyst(api, ticker),
            api.get(f"/securities/{ticker}/quote"),
            api.get(f"/securities/{ticker}/calendar"),
        )
    if analyst is None:
        raise ToolError(
            f"no analyst coverage published for '{ticker}' (funds and indices have none)"
        )
    d = _consensus(analyst, quote.get("price"), calendar)
    currency = (analyst.get("instrument") or {}).get("currency")
    return Result(
        EstimatesOutput(ticker=ticker, currency=currency, **d, notes=api.limits),
        Provenance(**provenance(subject=ticker)),
    )


SPEC = ToolSpec(
    name="data_equity_estimates",
    display_name="data.equity.estimates",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityEstimatesRequest,
    handler=_handler,
    output=EstimatesOutput,
    tags=("equity", "estimates", "consensus", "analyst", "data"),
)
