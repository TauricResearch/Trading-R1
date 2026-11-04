# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.corporate_actions: dividends, splits and fund distributions.

The event history Tauric records (dividends by ex-dividend date, split ratios, fund capital-gain
distributions), with two sums:
the trailing-twelve-month dividends (with the number
of payments, since a 365-day window can hold five ex-dates of a quarterly payer) and each
complete calendar year's total (the dividend growth record). Amounts per share in the listing
currency, adjusted for later splits. Capital-gain distributions are listed apart from the
dividends.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Number, Price, Text
from trading_r1.tools.data.common import Symbol, months_back
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Corporate actions for a stock or fund: dividends (by ex-dividend date, per share) and "
    "splits (ratio, e.g. 4.0 = 4-for-1) over the last `years`; plus trailing-12-month dividends "
    "(sum and number of payments; a 365-day window can hold five quarterly ex-dates) and each "
    "complete calendar year's total (the growth record; only years wholly inside the window). "
    "Fund capital-gain distributions are listed apart (`capital_gains`). Per-share amounts "
    "in the listing `currency`, adjusted for later splits (AAPL's 2012 dividends read "
    "about 1/28 of what was paid). A special dividend reads as a dividend and spin-offs are not "
    "recorded; the record shows what is held, so an absence here leaves open whether an action "
    "occurred. Paid history only: the next declared dividend's dates are in data.equity.earnings."
)
_TTM_DAYS = 365


class CorporateActionsRequest(BaseModel):
    """Input for the corporate-action history."""

    symbol: Symbol = Field(..., description="Stock or fund ticker (e.g. KO, AAPL, SPY).")
    years: int = Field(10, ge=1, le=50, description="Years of history to return.")


def _events(rows: list[dict[str, Any]], kind: str, since: dt.date, key: str) -> list[dict]:
    """Tauric corporate-action rows of one type as [{date, key: value}] from `since`, oldest
    first."""
    out = [
        {"date": r["ex_date"], key: r["value"]}
        for r in rows
        if r["action_type"] == kind and r.get("value") and r["ex_date"] >= since.isoformat()
    ]
    return sorted(out, key=lambda e: e["date"])


def dividend_sums(
    dividends: list[dict[str, Any]], today: dt.date, since: dt.date
) -> dict[str, Any]:
    """Trailing-twelve-month total and count, and the totals of calendar years wholly inside
    [since, today)."""
    cutoff = (today - dt.timedelta(days=_TTM_DAYS)).isoformat()
    ttm = [d["amount"] for d in dividends if cutoff < d["date"] <= today.isoformat()]
    years: dict[str, float] = {}
    for d in dividends:
        if since.year < int(d["date"][:4]) < today.year:  # the window's edge years are partial
            years[d["date"][:4]] = years.get(d["date"][:4], 0.0) + d["amount"]
    return {
        "ttm_dividends": sum(ttm) if ttm else None,
        "ttm_payments": len(ttm),
        "annual_dividends": years,
    }


def disputed(rows: list[dict[str, Any]], since: dt.date) -> set[tuple[str, str]]:
    """The (ex-date, action type) pairs reports disagree on: Tauric returns every report's value."""
    return {
        (r["ex_date"], r["action_type"])
        for r in rows
        if r["ex_date"] >= since.isoformat()
        and (r.get("field_states") or {}).get("assertion", {}).get("reason")
        == "conflicting_assertions"
    }


def _curate(body: dict[str, Any], since: dt.date) -> dict[str, Any]:
    rows = body.get("data") or []
    currency = next((r["currency"] for r in rows if r.get("currency")), None)
    out = {
        "currency": currency or (body.get("instrument") or {}).get("currency"),
        "dividends": _events(rows, "dividend", since, "amount"),
        "splits": _events(rows, "split", since, "ratio"),
    }
    gains = _events(rows, "capital_gain", since, "amount")
    if gains:
        out["capital_gains"] = gains
    return out


class Payment(BaseModel):
    date: Day
    amount: Price


class Split(BaseModel):
    date: Day
    ratio: Number  # 4.0 = 4-for-1


class CorporateActionsOutput(Output):
    """Per-share amounts in `currency`, adjusted for later splits; events oldest first."""

    ticker: Code
    currency: Code = None
    ttm_dividends: Price = None  # none recorded in the last 365 days
    ttm_payments: Count
    annual_dividends: dict[str, Price]  # complete calendar years inside the window
    splits: list[Split]
    capital_gains: list[Payment] = []
    dividends: list[Payment]
    notes: list[Text] = []


async def _handler(req: CorporateActionsRequest) -> Result:
    ticker = req.symbol
    today = dt.date.today()
    since = months_back(today, req.years * 12)  # 29 Feb clamps
    async with Tauric() as api:
        body = await api.get(f"/securities/{ticker}/corporate-actions")
    data = _curate(body, since)
    if not (data["dividends"] or data["splits"] or data.get("capital_gains")):
        raise ToolError(
            f"Tauric holds no dividends, splits or distributions for '{ticker}' in the last "
            f"{req.years} years; that leaves open whether any occurred"
        )
    conflicts = disputed(body.get("data") or [], since)
    undisputed = [d for d in data["dividends"] if (d["date"], "dividend") not in conflicts]
    notes = [
        f"Reports disagree on the {kind} of {day}: every reported value is listed"
        + (", none of them in the sums." if kind == "dividend" else ".")
        for day, kind in sorted(conflicts)
    ]
    out = CorporateActionsOutput(
        ticker=ticker,
        **data,
        **dividend_sums(undisputed, today, since),
        notes=notes + api.limits,
    )
    return Result(out, Provenance(**provenance(subject=ticker)))


SPEC = ToolSpec(
    name="data_equity_corporate_actions",
    display_name="data.equity.corporate_actions",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CorporateActionsRequest,
    handler=_handler,
    output=CorporateActionsOutput,
    tags=("equity", "dividends", "splits", "corporate-actions", "data"),
)
