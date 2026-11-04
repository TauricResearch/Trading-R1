# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.fundamentals: financial summary, and the financial statements.

``statement`` "summary" (default): trailing-twelve-month financials from the SEC filings plus
the profile's balance-sheet snapshot and a 4-year income trend (revenue / operating income / net
income / EBITDA), which feeds the DCF. "income", "balance" or "cashflow": that statement's curated
lines by period, annual or quarterly, oldest first, with named per-period ratios
(`statements.py`): trends, earnings quality, and inputs for the modeling tools. Numbers only;
the model interprets.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

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
from trading_r1.contract.units import Code, Day, Fraction, Money, Multiple, Number, Price, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import snapshot, statements
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Fundamental financials for a public equity: revenue, EBITDA, margins, free and operating "
    "cash flow, total cash and debt, net_debt, ROE, growth and EPS, plus a multi-year income "
    "trend. Flows are trailing twelve months from SEC filings (`as_of` their latest period); "
    "cash, debt, ROE and growth are Tauric's current profile. Feeds DCF: `ttm.ebitda` into "
    "terminal_ebitda, `ttm.net_debt` into net_debt. Derived figures carry `rules` (how "
    "computed) and `concepts` (the filed concept); banks have no gross profit or EBITDA. "
    "With statement income, balance or cashflow (annual, up to 8 years, or quarterly, up to 8 "
    "quarters): that statement's line items as series aligned to `periods`, oldest first "
    "(`lines.revenue`, `lines.total_debt`, …; absent lines are omitted), `checks` where a "
    "filed line breaks its accounting identity, and `derived` ratios and growth per period. "
    "Amounts are in `currency`, the reporting currency."
)
Statement = Literal["summary", "income", "balance", "cashflow"]


class EquityFundamentalsRequest(BaseModel):
    """Input for fetching equity fundamentals and income-statement history."""

    symbol: Symbol = Field(..., description="Equity ticker (e.g. NVDA).")
    statement: Statement = Field(
        "summary", description="summary (TTM + income trend), income, balance or cashflow."
    )
    cadence: Literal["annual", "quarterly"] = Field(
        "annual", description="Statement cadence (statements only)."
    )
    periods: int = Field(
        4, ge=1, le=8, description="Most recent periods to return (statements only)."
    )


_UNITS = {"flow": "money", "stock": "money", "per_share": "price", "count": "count"}


class Ttm(BaseModel):
    """The summary's trailing figures: flows from the filings, balance sheet and ratios from
    the profile, EPS from the filings and the consensus."""

    total_revenue: Money
    ebitda: Money
    gross_margin: Fraction
    operating_margin: Fraction
    profit_margin: Fraction
    free_cashflow: Money
    operating_cashflow: Money
    total_cash: Money
    total_debt: Money
    net_debt: Money = None
    net_debt_to_ebitda: Multiple = None
    return_on_equity: Fraction
    revenue_growth: Fraction
    earnings_growth: Fraction
    trailing_eps: Price
    forward_eps: Price
    price_to_book: Multiple


class TrendYear(BaseModel):
    period: Day
    revenue: Money = None
    operating_income: Money = None
    net_income: Money = None
    ebitda: Money = None


class Check(BaseModel):
    """A filed line that disagrees with its accounting identity."""

    line: Code
    period: Day
    filed: Money
    derived: Money
    rule: Text


class FundamentalsOutput(Output):
    """The summary (`ttm`, `income_trend`) or one statement (`lines` and `derived` aligned to
    `periods`, units per line in `units`)."""

    model_config = rendered(index="periods")

    ticker: Code
    statement: Code
    period: Code = None
    currency: Code = None
    as_of: Day = None
    note: Text = None
    notes: list[Text] = []  # what Tauric states limits the figures
    ttm: Ttm | None = None
    income_trend: list[TrendYear] | None = None
    periods: list[Day] | None = None
    lines: dict[str, list[Number]] | None = None
    derived: dict[str, list[Number]] | None = None
    units: dict[str, Code] | None = None
    rules: dict[str, Text] = {}
    concepts: Annotated[dict[str, Code], hint(hidden=True)] = {}
    checks: list[Check] = []


def _basis(basis: dict[str, str]) -> dict[str, dict[str, str]]:
    """How each line was found, split: the lines a rule computed (in any period), with the
    rule, and the lines filed as such, with their concepts."""
    rules = {k: b for k, b in basis.items() if statements.DERIVED in b}
    return {"rules": rules, "concepts": {k: b for k, b in basis.items() if k not in rules}}


def _income_trend(annual: dict[str, Any]) -> list[TrendYear]:
    """Revenue, operating income, net income and EBITDA by fiscal year, newest first."""
    lines, periods = annual["lines"], annual["periods"]
    return [
        TrendYear(period=periods[i], **{k: lines[k][i] for k in _TREND_LINES if k in lines})
        for i in reversed(range(len(periods)))
    ]


def _units(statement: str, series: dict[str, Any]) -> dict[str, str]:
    units = {k: _UNITS[statements.line(statement, k).kind] for k in series["lines"]}
    return units | {
        k: "multiple" if k in statements.MULTIPLES else "fraction" for k in series["derived"]
    }


_TREND_LINES = ("revenue", "operating_income", "net_income", "ebitda")
_NOT_SERVED = (
    "SEC statements are served for US-GAAP filers; funds, indices and IFRS filers (most ADRs, "
    "e.g. TSM) have none"
)


async def _statement(req: EquityFundamentalsRequest, ticker: str) -> Result:
    quarterly = req.cadence == "quarterly"
    async with Tauric() as api:
        facts = await statements.load_facts(
            api, ticker, req.statement, statements.limits_for(req.statement, quarterly, req.periods)
        )
    if req.statement == "balance" and not quarterly:
        facts["instant"] = facts["annual"]  # fiscal year-end balances
    series = statements.statement_series(facts, req.statement, req.periods, quarterly)
    if not series["lines"]:
        raise ToolError(f"no {req.cadence} {req.statement} statement for '{ticker}': {_NOT_SERVED}")
    out = FundamentalsOutput(
        ticker=ticker,
        statement=req.statement,
        period=req.cadence,
        currency=statements.currency(next(iter(facts.values()))),
        notes=api.limits,
        as_of=series["periods"][-1],
        units=_units(req.statement, series),
        periods=series["periods"],
        lines=series["lines"],
        derived=series["derived"],
        checks=series["checks"],
        **_basis(series["basis"]),
    )
    return Result(
        out,
        Provenance(
            **provenance(
                subject=ticker,
                view=f"{req.statement} {req.cadence}",
                as_of=out.as_of,
                source="SEC filings",
            )
        ),
    )


def _first(*values: float | None) -> float | None:
    return next((v for v in values if v is not None), None)


def _ttm(prof: dict[str, Any], cons: dict[str, Any] | None, ttm: dict[str, Any]) -> Ttm:
    rev = ttm.get("revenue")

    def over_revenue(key: str) -> float | None:
        return ttm[key] / rev if rev and key in ttm else None

    debt, cash = prof.get("total_debt"), prof.get("total_cash")
    return Ttm(
        total_revenue=rev,
        ebitda=ttm.get("ebitda"),
        gross_margin=over_revenue("gross_profit"),
        operating_margin=_first(over_revenue("operating_income"), prof.get("operating_margin")),
        profit_margin=_first(over_revenue("net_income"), prof.get("profit_margin")),
        free_cashflow=ttm.get("free_cash_flow"),
        operating_cashflow=ttm.get("operating_cash_flow"),
        total_cash=cash,
        total_debt=debt,
        return_on_equity=prof.get("return_on_equity"),
        revenue_growth=prof.get("revenue_growth"),
        earnings_growth=prof.get("earnings_growth"),
        trailing_eps=_first(ttm.get("diluted_eps"), (cons or {}).get("trailing_eps")),
        forward_eps=(cons or {}).get("forward_eps"),
        price_to_book=prof.get("price_to_book"),
        **snapshot.leverage(debt, cash, ttm.get("ebitda")),
    )


async def _handler(req: EquityFundamentalsRequest) -> Result:
    ticker = req.symbol
    if req.statement != "summary":
        return await _statement(req, ticker)
    async with Tauric() as api:
        prof, cons, ttm, annual = await asyncio.gather(
            snapshot.profile(api, ticker),
            snapshot.analyst(api, ticker),
            snapshot.ttm_figures(api, ticker),
            statements.load_facts(
                api, ticker, "income", statements.limits_for("income", False, 4), _TREND_LINES
            ),
        )
    figures = _ttm(prof, cons, ttm)
    if not figures.model_dump(exclude_none=True):
        raise ToolError(f"no fundamentals for '{ticker}': check the ticker")
    out = FundamentalsOutput(
        ticker=ticker,
        statement="summary",
        currency=ttm.get("currency"),
        as_of=ttm.get("as_of"),
        notes=api.limits,
        note=None
        if ttm
        else f"No filing-based figures: {_NOT_SERVED}; balance sheet and ratios are the profile's.",
        ttm=figures,
        income_trend=_income_trend(
            statements.statement_series(annual, "income", 4, quarterly=False, keys=_TREND_LINES)
        ),
        **_basis(ttm.get("basis", {})),
    )
    return Result(
        out,
        Provenance(
            **provenance(
                subject=ticker,
                view="summary",
                as_of=out.as_of,
                source="SEC filings; Tauric profile" if ttm else "Tauric profile",
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_fundamentals",
    display_name="data.equity.fundamentals",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityFundamentalsRequest,
    handler=_handler,
    output=FundamentalsOutput,
    tags=("equity", "fundamentals", "financials", "data"),
)
