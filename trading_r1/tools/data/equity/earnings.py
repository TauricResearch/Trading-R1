# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.earnings: the earnings record and the next report.

Past quarters from the SEC filings: reported diluted EPS, revenue and net income by fiscal
quarter (a fiscal Q4 is the year less its nine months), with the change on the quarter a year
earlier. The next report: its date, the consensus EPS and revenue estimates, and the dividend
dates, from Tauric's calendar. Consensus estimates and surprises for past quarters are not
served. Reported facts and consensus numbers only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Fraction, Money, Price, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import statements
from trading_r1.tools.data.tauric import NOT_PUBLISHED, Tauric, provenance

_DESCRIPTION = (
    "Earnings record and next report for a public equity: per fiscal quarter the reported "
    "diluted EPS, revenue and net income from the filings, with EPS and revenue growth on the "
    "quarter a year earlier (newest first; a fiscal Q4's EPS is not filed and is left out); "
    "and the report on the calendar (next, or latest until the next is set) with its "
    "consensus EPS and revenue estimates; the "
    "dividend on the calendar (next or last; its amount when announced). Consensus estimates "
    "and surprises for past quarters are not available. Quarters are given by period end, with "
    "no fiscal quarter name. Amounts are in `currency`, the "
    "reporting currency. For ratings and price targets use data.equity.estimates."
)
_LINES = ("revenue", "net_income", "diluted_eps")
_NEXT = {
    "date": "earnings_date",
    "eps_estimate": "eps_estimate",
    "revenue_estimate": "revenue_estimate",
}


class EquityEarningsRequest(BaseModel):
    """Input for the earnings record."""

    symbol: Symbol = Field(..., description="Equity ticker (e.g. AAPL).")
    quarters: int = Field(8, ge=1, le=12, description="Past quarters to return, newest first.")


async def _calendar(api: Tauric, ticker: str) -> tuple[dict[str, Any], str | None]:
    """The calendar and, when it could not be read, why: what Tauric does not publish is
    absent, and any other failure leaves the next report and dividend unknown (the filings still
    answer)."""
    try:
        return await api.get(f"/securities/{ticker}/calendar"), None
    except ToolError as err:
        if err.code in NOT_PUBLISHED:
            return {}, None
        return {}, f"the calendar could not be read ({err}): next report and dividend unknown"


class Quarter(BaseModel):
    period_end: Day
    eps: Price
    revenue: Money
    net_income: Money
    eps_growth_yoy: Fraction
    revenue_growth_yoy: Fraction


class Report(BaseModel):
    """The report on the calendar: next (`upcoming`), or the latest until the next is set."""

    date: Day = None
    upcoming: bool
    eps_estimate: Price = None
    revenue_estimate: Money = None


class Dividend(BaseModel):
    ex_date: Day
    payment_date: Day = None
    amount: Price = None  # not yet announced
    upcoming: bool


class EarningsOutput(Output):
    """Amounts in `currency`, the reporting currency; quarters newest first."""

    ticker: Code
    currency: Code = None
    asked: Count
    next_report: Report | None = None
    dividends: list[Dividend] = []
    history: list[Quarter]
    notes: list[Text] = []  # what is missing, and why


def _notes(history: list, asked: int, currency: str | None, report: dict | None) -> list[str]:
    out = []
    if not history:
        out.append(
            f"no quarterly figures from the filings (0 of {asked} quarters): a filer outside "
            "US-GAAP, or a fund"
        )
    if report and currency is None:
        out.append(
            "the calendar states no currency for its estimates; a foreign filer's revenue "
            "estimate may be in its reporting currency"
        )
    if report and not report["upcoming"] and report.get("eps_actual") is None:
        out.append("the latest report's actuals are not published yet")
    return out


def _history(series: dict[str, Any]) -> list[dict[str, Any]]:
    lines, derived = series["lines"], series["derived"]

    def at(source: dict[str, list], key: str, i: int) -> float | None:
        return source[key][i] if key in source else None

    return [
        {
            "period_end": series["periods"][i],
            "eps": at(lines, "diluted_eps", i),
            "revenue": at(lines, "revenue", i),
            "net_income": at(lines, "net_income", i),
            "eps_growth_yoy": at(derived, "diluted_eps_growth_yoy", i),
            "revenue_growth_yoy": at(derived, "revenue_growth_yoy", i),
        }
        for i in reversed(range(len(series["periods"])))
    ]


async def _handler(req: EquityEarningsRequest) -> Result:
    ticker = req.symbol
    n = req.quarters + 4  # a year of earlier quarters, for growth on the year before
    limits = statements.limits_for("income", True, n)
    async with Tauric() as api:
        (calendar, calendar_failure), facts = await asyncio.gather(
            _calendar(api, ticker), statements.load_facts(api, ticker, "income", limits, _LINES)
        )
    series = statements.statement_series(facts, "income", n, quarterly=True, keys=_LINES)
    history = _history(series)[: req.quarters]
    earnings = calendar.get("earnings") or {}
    today = dt.date.today().isoformat()
    next_report = {k: earnings[f] for k, f in _NEXT.items() if earnings.get(f) is not None}
    if next_report:
        next_report["upcoming"] = next_report.get("date", today) >= today
    if not history and not next_report:
        raise ToolError(f"no earnings data for '{ticker}': funds and indices have none")
    currency = statements.currency(facts["quarterly"] | facts["annual"])
    out = EarningsOutput(
        ticker=ticker,
        currency=currency,
        history=history,
        asked=req.quarters,
        notes=_notes(history, req.quarters, currency, next_report or None)
        + ([calendar_failure] if calendar_failure else [])
        + api.limits,
        next_report=next_report or None,
        dividends=[
            {k: d.get(k) for k in ("ex_date", "payment_date", "amount")}
            | {"upcoming": (d.get("ex_date") or "") >= today}
            for d in calendar.get("dividends") or []
            if d.get("ex_date")
        ],
    )
    return Result(
        out,
        Provenance(
            **provenance(
                subject=ticker,
                as_of=history[0]["period_end"] if history else None,
                source="SEC filings" if history else None,
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_earnings",
    display_name="data.equity.earnings",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityEarningsRequest,
    handler=_handler,
    output=EarningsOutput,
    tags=("equity", "earnings", "calendar", "data"),
)
