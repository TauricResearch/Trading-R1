# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.ownership: who holds a stock, what insiders are doing, and short interest.

Three views of Tauric's ownership data, one per call: ``holders`` (the insider / institution
split and the largest institutional holders from their 13F filings, for a quarter, optionally
against the one before), ``insiders`` (Form 4 transactions and the purchase/sale totals over a
window, six months unless asked, whole and by insider) and ``short_interest`` (shares short, days
to cover, and short interest as a share of the float).

What the figures are: a holder's value is the position's value at the report date; the
institutional list is US 13F filers (filed up to ~45 days after the quarter) and excludes the
long positions of non-filers; insider amounts are in the stock's currency; and "purchases" are
open-market code-P transactions only, "sales" code-S only; awards, exercises and gifts are
listed in the transactions but not totalled as trades.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Day, Fraction, Money, Number, Price, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import snapshot
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Ownership of a public equity, one view per call. holders (default): % held by insiders "
    "and institutions, and the largest institutional holders from their 13F filings: manager, "
    "shares and value at the report date summed over the manager's filing lines, filing and "
    "report dates (filings lag up to ~45 days "
    "after the quarter; US 13F filers only, so a non-US ordinary may show a few small ones). "
    "`date` reads an earlier quarter (`quarters` lists those served); `compare` adds each "
    "manager's change in shares from the quarter before. "
    "insiders: recent insider transactions (insider, role, date, code and its meaning, shares, "
    "price, value, shares owned after) and totals of open-market purchases (code P) and sales "
    "(code S), whole and `by_insider`, over six months or the window `start` and `end` set; "
    "awards, exercises and gifts are listed but not counted as trades. Tauric serves about the "
    "past year of transactions; `served_from` gives the date they begin. "
    "short_interest: shares short, days to cover (short ratio) and short interest as a share "
    "of the float (US listings; the prior month's figure is not available)."
)
View = Literal["holders", "insiders", "short_interest"]
_PAGE = 14  # Tauric's most insider transactions per response
_HOLDERS = 10  # managers shown
_HOLDER_LINES = 36  # Tauric's most 13F lines per response, by value
_TOTAL_MONTHS = 6
_MAX_PAGES = 10  # a backstop on the six-month totals: 140 transactions
_MAX_WINDOW_PAGES = 50  # a window asked for by date: 700 transactions
_TRADERS_SHOWN = 30  # a window's insiders, in full
_TRADE = ("count", "shares", "value")


class OwnershipRequest(BaseModel):
    """Input for ownership data."""

    symbol: Symbol = Field(..., description="Equity ticker (e.g. AAPL).")
    view: View = Field("holders", description="holders, insiders or short_interest.")
    limit: int = Field(
        14, ge=1, le=_PAGE, description="insiders view: most recent transactions to return."
    )
    start: dt.date | None = Field(
        None, description="insiders view: first transaction date (default: six months back)."
    )
    end: dt.date | None = Field(
        None, description="insiders view: last transaction date (default: the latest)."
    )
    date: dt.date | None = Field(
        None, description="holders view: the 13F quarter-end to read, e.g. 2026-03-31."
    )
    compare: bool = Field(
        False,
        description="holders view: add each manager's change in shares from the quarter before.",
    )


class Manager(BaseModel):
    manager: Code
    shares: Count
    value: Money
    lines: Count
    filed: Day
    report_date: Day
    # against `compared_to`; empty when the manager is not among that quarter's largest lines
    shares_change: Count = None


class Holders(BaseModel):
    insiders: Fraction = None
    institutions: Fraction = None
    lines_read: Count = None  # the largest lines the managers were found among
    report_period: Day = None
    period_state: Code = None  # how complete Tauric says this quarter's filings are
    compared_to: Day = None
    quarters: list[Day] = []  # the quarters Tauric serves, newest first
    managers: list[Manager] = []


class Transaction(BaseModel):
    date: Day
    insider: Code
    role: Code
    code: Code
    meaning: Code
    shares: Count
    price: Price
    value: Money
    owned_after: Count
    disposed: bool
    derivative: bool | None
    planned: bool | None


class Trades(BaseModel):
    count: Count
    shares: Count
    value: Money
    unpriced: Count


class Totals(BaseModel):
    purchases: Trades
    sales: Trades
    net_shares: Count


class Trader(BaseModel):
    insider: Code
    role: Code
    sales_count: Count
    sales_shares: Count
    sales_value: Money
    purchases_count: Count
    purchases_shares: Count
    purchases_value: Money


class Insiders(BaseModel):
    window_start: Day
    window_end: Day = None
    served_from: Day = None  # the earliest transaction date Tauric serves
    count: Count
    complete: bool
    notes: list[Text] = []
    totals: Totals
    by_insider: Annotated[list[Trader], hint(rows=_TRADERS_SHOWN)] = []  # largest value first
    transactions: list[Transaction]


class ShortInterest(BaseModel):
    shares_short: Count = None
    days_to_cover: Number = None
    short_of_float: Fraction = None


class OwnershipOutput(Output):
    """One view: `holders`, `insiders` or `short_interest`."""

    ticker: Code
    view: Code
    holders: Holders | None = None
    insiders: Insiders | None = None
    short_interest: ShortInterest | None = None
    notes: list[Text] = []


def _managers(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The largest managers by CIK. A 13F report is filed as lines (one per class, discretion and
    co-manager), and each filing states its totals in the security; a manager's holding is the
    sum over its current filings: its latest original or restatement plus the new-holdings
    amendments after it, as Tauric marks them. The largest lines find the managers."""
    managers: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for h in body.get("data") or []:
        acc = h["accession"]
        f = (body.get("filings") or {}).get(acc) or {}
        if acc in seen or f.get("view_state", "current") != "current":
            continue  # counted, or an amendment Tauric serves for the reader to judge
        seen.add(acc)
        who, totals = f.get("manager") or {}, f.get("security_totals") or {}
        m = managers.setdefault(
            who.get("cik") or acc,
            {
                "manager": who.get("name"),
                "shares": 0,
                "value": 0,
                "lines": 0,
                "filed": None,
                "report_date": f.get("report_date"),
            },
        )
        m["shares"] += totals.get("shares") or 0
        m["value"] += totals.get("value") or 0
        m["lines"] += totals.get("position_lines") or 0
        m["filed"] = max(filter(None, (m["filed"], f.get("filed"))), default=None)
    return managers


def _quarters(body: dict[str, Any]) -> list[str]:
    return [q["period"] for q in (body.get("coverage") or {}).get("periods") or []]


def _quarter_before(body: dict[str, Any]) -> str | None:
    """The quarter Tauric serves just before the one read (`periods` run newest first)."""
    quarters = _quarters(body)
    period = (body.get("coverage") or {}).get("period")
    i = quarters.index(period) + 1 if period in quarters else len(quarters)
    return quarters[i] if i < len(quarters) else None


def _holders(
    profile: dict[str, Any], body: dict[str, Any], before: dict[str, Any] | None
) -> dict[str, Any]:
    """The largest managers of a quarter, each against the quarter `before` when it is given."""
    managers = _managers(body)
    earlier = _managers(before) if before else {}
    for cik, m in managers.items():
        if cik in earlier:
            m["shares_change"] = m["shares"] - earlier[cik]["shares"]
    rows = sorted(managers.values(), key=lambda m: m["value"], reverse=True)[:_HOLDERS]
    cov = body.get("coverage") or {}
    return {
        "insiders": profile.get("held_fraction_insiders"),
        "institutions": profile.get("held_fraction_institutions"),
        "lines_read": len(body.get("data") or []),
        "report_period": cov.get("period"),
        "period_state": cov.get("period_state"),
        "compared_to": (before.get("coverage") or {}).get("period") if before else None,
        "quarters": _quarters(body),
        "managers": rows,
    }


async def _holdings(
    api: Tauric, ticker: str, period: dt.date | None, compare: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """A quarter's largest 13F lines (the latest unless `period` names one) and, when asked, the
    quarter before's."""

    async def lines(quarter: str | None) -> dict[str, Any]:
        return await api.get(
            f"/securities/{ticker}/institutional-holders",
            limit=_HOLDER_LINES,
            position="shares",
            period=quarter,
        )

    body = await lines(period.isoformat() if period else None)
    state = (body.get("coverage") or {}).get("period_state")
    if period and (state == "not_covered" or not body.get("data")):
        raise ToolError(
            f"no 13F holders of {ticker} for {period}: Tauric serves the quarters "
            + ", ".join(_quarters(body))
        )
    before = _quarter_before(body) if compare else None
    return body, await lines(before) if before else None


def _role(owners: list[dict[str, Any]]) -> str | None:
    o = owners[0] if owners else {}
    if o.get("officer_title"):
        return o["officer_title"]
    return next(
        (
            r
            for k, r in (("is_director", "Director"), ("is_ten_percent_owner", "10% owner"))
            if o.get(k)
        ),
        None,
    )


def _transaction(t: dict[str, Any], filings: dict[str, Any]) -> dict[str, Any]:
    f = filings.get(t["accession"]) or {}
    owners = f.get("owners") or []
    shares, price = t.get("shares"), t.get("price")
    return {
        "insider": owners[0]["name"] if owners else None,
        "role": _role(owners),
        "date": t.get("transaction_date"),
        "code": t.get("code"),
        "meaning": t.get("code_meaning"),
        "shares": shares,
        "price": price,
        "value": shares * price if shares is not None and price else None,
        "owned_after": t.get("owned_following"),
        "disposed": t.get("acquired_disposed") == "D",
        "derivative": t.get("derivative"),
        "planned": f.get("rule_10b5_1"),
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Open-market purchases (code P) and sales (code S) over the rows."""
    out: dict[str, Any] = {}
    for label, code in (("purchases", "P"), ("sales", "S")):
        trades = [r for r in rows if r["code"] == code and not r["derivative"]]
        out[label] = {
            "count": len(trades),
            "shares": sum(r["shares"] or 0 for r in trades),
            "value": sum(r["value"] or 0 for r in trades),
            "unpriced": sum(r["value"] is None for r in trades),
        }
    out["net_shares"] = out["purchases"]["shares"] - out["sales"]["shares"]
    return out


def traders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each insider's open-market trades over the rows, largest value first."""
    out = []
    for who in dict.fromkeys(r["insider"] for r in rows):
        own = [r for r in rows if r["insider"] == who]
        t = totals(own)
        if t["sales"]["count"] or t["purchases"]["count"]:
            out.append(
                {"insider": who, "role": own[0]["role"]}
                | {f"{side}_{k}": t[side][k] for side in ("sales", "purchases") for k in _TRADE}
            )
    return sorted(out, key=lambda t: t["sales_value"] + t["purchases_value"], reverse=True)


async def _insiders(
    api: Tauric, ticker: str, n: int, start: dt.date | None, end: dt.date | None
) -> dict[str, Any]:
    default = (end or dt.date.today()) - dt.timedelta(days=round(_TOTAL_MONTHS * 30.4))
    since = (start or default).isoformat()
    last = end.isoformat() if end else None
    path = f"/securities/{ticker}/insider-transactions"
    rows: list[dict[str, Any]] = []
    after = None
    served_from = None
    for _ in range(_MAX_WINDOW_PAGES if start or end else _MAX_PAGES):
        page = await api.get(path, start=since, end=last, limit=_PAGE, after=after)
        served_from = (page.get("coverage") or {}).get("from")
        if last and served_from and last < served_from:
            raise ToolError(
                f"Tauric serves {ticker}'s insider transactions from {served_from}, so none "
                f"through {last} can be read"
            )
        filings = page.get("filings") or {}
        rows += [
            _transaction(t, filings)
            for t in page.get("data") or []
            if t.get("kind", "transaction") == "transaction"
        ]
        after = page.get("next_cursor")
        if after is None:
            break
    complete = after is None
    early = bool(served_from) and since < served_from
    return {
        "transactions": rows[:n],
        # a capped count covers only the newest rows: the window starts at the oldest counted
        "window_start": (
            (served_from if early else since)
            if complete or not rows
            else min(r["date"] for r in rows)
        ),
        "window_end": last,
        "served_from": served_from,
        "notes": [
            f"Tauric serves these transactions from {served_from}: the part of the window before "
            f"it, from {since}, is not counted."
        ]
        if early
        else [],
        "totals": totals(rows),
        "by_insider": traders(rows),
        "complete": complete,
        "count": len(rows),
    }


def _short(profile: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "shares_short": "shares_short",
        "days_to_cover": "days_to_cover",
        "short_of_float": "short_fraction_of_float",
    }
    return {k: profile[f] for k, f in fields.items() if profile.get(f) is not None}


_SOURCES = {"holders": "SEC 13F", "insiders": "SEC Form 4", "short_interest": None}


def _as_of(view: str, data: dict[str, Any]) -> str | None:
    if view == "holders":
        return data.get("report_period")
    if view == "insiders":
        return max((r["date"] for r in data.get("transactions") or [] if r["date"]), default=None)
    return None


async def _handler(req: OwnershipRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        if req.view == "holders":
            profile, (body, before) = await asyncio.gather(
                snapshot.profile(api, ticker), _holdings(api, ticker, req.date, req.compare)
            )
            data = _holders(profile, body, before)
        elif req.view == "insiders":
            data = await _insiders(api, ticker, req.limit, req.start, req.end)
        else:
            data = _short(await snapshot.profile(api, ticker))
    if req.view != "insiders" and not any(v for v in data.values()):
        raise ToolError(f"no {req.view.replace('_', ' ')} data for '{ticker}'")
    return Result(
        OwnershipOutput(ticker=ticker, view=req.view, **{req.view: data}, notes=api.limits),
        Provenance(
            **provenance(
                subject=ticker,
                view=req.view,
                as_of=_as_of(req.view, data),
                source=_SOURCES[req.view],
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_ownership",
    display_name="data.equity.ownership",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=OwnershipRequest,
    handler=_handler,
    output=OwnershipOutput,
    tags=("equity", "ownership", "holders", "insiders", "short-interest", "data"),
)
