# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.ratios: financial-health ratios + valuation multiples.

Complements `data.equity.fundamentals` (size, margins, cash flow) with the ratio
view an analyst reaches for: liquidity, leverage, returns on assets/equity, and the
company's own valuation multiples. Ratios as Tauric's profile publishes them, EV multiples
from enterprise value over trailing-twelve-month filings figures (`company.py`), and a couple
of safe derived figures (net debt, net-debt/EBITDA). Numbers only; the model interprets and
compares.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Day, Fraction, Money, Multiple, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import company, snapshot
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Financial-health ratios and valuation multiples for a public equity: liquidity "
    "(current, quick), leverage (debt/equity, net debt, net-debt/EBITDA), returns "
    "(ROA, ROE), and multiples (P/E trailing & forward, EV/EBITDA, EV/revenue, P/S, "
    "PEG, dividend yield; EV/EBITDA and net-debt/EBITDA need trailing-year EBITDA, which banks "
    "do not have and a few filers' served facts cannot give; "
    "a company whose statements are in another currency than its listing has no EV "
    "multiples, P/S or net debt). Complements fundamentals "
    "for valuation and credit work."
)

# our snake_case key -> Tauric profile field (ratios already in usable form: fractions)
_RATIO_MAP = {
    "current_ratio": "current_ratio",
    "quick_ratio": "quick_ratio",
    "return_on_assets": "return_on_assets",
    "return_on_equity": "return_on_equity",
    "trailing_pe": "pe_ratio",
    "forward_pe": "forward_pe",
    "price_to_sales": "price_to_sales",
    "price_to_book": "price_to_book",
    "peg_ratio": "peg_ratio",
    "dividend_yield": "dividend_yield",
    "payout_ratio": "payout_ratio",
}


class EquityRatiosRequest(BaseModel):
    """Input for fetching ratio and valuation-multiple snapshots."""

    symbol: Symbol = Field(..., description="Equity ticker (e.g. NVDA).")


class Ratios(BaseModel):
    current_ratio: Multiple
    quick_ratio: Multiple
    debt_to_equity: Multiple
    net_debt: Money = None  # in `currency`; withheld when the statements are in another
    net_debt_to_ebitda: Multiple = None  # needs positive trailing-year EBITDA
    return_on_assets: Fraction
    return_on_equity: Fraction
    trailing_pe: Multiple
    forward_pe: Multiple
    ev_to_ebitda: Multiple
    ev_to_revenue: Multiple
    price_to_sales: Multiple
    price_to_book: Multiple
    peg_ratio: Multiple
    dividend_yield: Fraction = None  # a non-payer has none
    payout_ratio: Fraction = None


class RatiosOutput(Output):
    ticker: Code
    currency: Code = None
    valuation_as_of: Day = None  # the close the multiples and market cap rest on
    ratios: Ratios
    notes: list[Text] = []


def _ratios(c: company.Company) -> dict[str, Any]:
    out = {k: c.profile.get(pk) for k, pk in _RATIO_MAP.items()}
    out["debt_to_equity"] = c.profile.get("debt_to_equity")
    m = c.multiples()
    out["ev_to_ebitda"], out["ev_to_revenue"] = m.get("ev_ebitda"), m.get("ev_rev")
    if c.net_debt is not None:
        out |= snapshot.leverage(
            c.profile.get("total_debt"), c.profile.get("total_cash"), c.ttm.get("ebitda")
        )
    return out


async def _handler(req: EquityRatiosRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        c = await company.load(api, ticker)
    ratios = _ratios(c)
    if all(v is None for v in ratios.values()):
        raise ToolError(f"no ratio data for '{ticker}': funds and indices publish none")
    return Result(
        RatiosOutput(
            ticker=ticker,
            currency=c.currency,
            valuation_as_of=c.valuation_date,
            ratios=Ratios(**ratios),
            notes=c.notes + api.limits,
        ),
        Provenance(
            **provenance(subject=ticker, as_of=c.valuation_date or c.quote.get("session_date"))
        ),
    )


SPEC = ToolSpec(
    name="data_equity_ratios",
    display_name="data.equity.ratios",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityRatiosRequest,
    handler=_handler,
    output=RatiosOutput,
    tags=("equity", "ratios", "valuation", "credit", "data"),
)
