# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.reference: identity / classification / market snapshot."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Money, Multiple, Number, Price, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.equity import company
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Reference data for a public equity: name, GICS sector/industry group/industry/sub-industry, "
    "country, exchange (MIC), market cap, enterprise value, shares outstanding and float "
    "(shares available to trade), beta, current price, 52-week range, and P/E "
    "(snake_case keys: chain `prev:<id>.beta` into wacc and `prev:<id>.shares_outstanding` "
    "into dcf). Use to identify and classify a ticker (see data.equity.ratios for full "
    "multiples incl. dividend yield)."
)
# Output keys are snake_case (our_key -> Tauric profile field), consistent with every other data
# tool, so a chain path is uniform across the tools (prev:<id>.shares_outstanding).
_PROFILE_FIELDS = {
    "sector": "sector",
    "industry_group": "industry_group",
    "industry": "industry",
    "sub_industry": "sub_industry",
    "country": "country",
    "long_business_summary": "business_summary",
    "website": "website",
    "employees": "employees",
    "market_cap": "market_cap",
    "enterprise_value": "enterprise_value",
    "shares_outstanding": "shares_outstanding",
    "float_shares": "float_shares",
    "beta": "beta",
    "trailing_pe": "pe_ratio",
    "forward_pe": "forward_pe",
}


class EquityReferenceRequest(BaseModel):
    """Input for the equity reference-data tool."""

    symbol: Symbol = Field(..., description="Public equity ticker (e.g. NVDA, AAPL, BRK.B).")


class ReferenceOutput(Output):
    """Identity and classification, then the market snapshot; amounts and prices in
    `currency`, the listing's."""

    ticker: Code
    long_name: Code = None
    currency: Code = None
    exchange: Code = None
    quote_type: Code = None
    sector: Code = None
    industry_group: Code = None
    industry: Code = None
    sub_industry: Code = None
    country: Code = None
    website: Code = None
    employees: Count = None
    valuation_as_of: Day = None  # the close the market cap, enterprise value and P/E rest on
    market_cap: Money
    enterprise_value: Money = None
    shares_outstanding: Count
    float_shares: Count = None
    beta: Number
    current_price: Price
    fifty_two_week_high: Price = None
    fifty_two_week_low: Price = None
    trailing_pe: Multiple = None
    forward_pe: Multiple = None
    long_business_summary: Text = None  # its lead: what the company does
    notes: list[Text] = []


def _curate(c: company.Company) -> dict[str, Any]:
    instrument = c.profile.get("instrument") or {}
    out: dict[str, Any] = {"ticker": c.ticker, "long_name": c.name, "currency": c.currency}
    out["exchange"] = instrument.get("venue")
    out["quote_type"] = (instrument.get("classification") or {}).get("instrument_type")
    out |= {k: c.profile.get(pk) for k, pk in _PROFILE_FIELDS.items()}
    out["valuation_as_of"] = c.valuation_date
    out["current_price"] = c.price
    span = c.quote.get("range_52w") or {}
    out["fifty_two_week_high"], out["fifty_two_week_low"] = span.get("high"), span.get("low")
    out["long_business_summary"] = _lead(out["long_business_summary"])
    return out


_LEAD_CHARS = 600  # the products and segments; the rest is history and channels


def _lead(text: str | None) -> str | None:
    """The summary's opening sentences, up to `_LEAD_CHARS`."""
    if not text or len(text) <= _LEAD_CHARS:
        return text
    cut = text[:_LEAD_CHARS]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > 0 else cut.rstrip() + "…"


async def _handler(req: EquityReferenceRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        c = await company.load(api, ticker, filings=False)
    return Result(
        ReferenceOutput(**_curate(c), notes=c.notes + api.limits),
        Provenance(**provenance(subject=ticker, as_of=c.quote.get("session_date"))),
    )


SPEC = ToolSpec(
    name="data_equity_reference",
    display_name="data.equity.reference",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityReferenceRequest,
    handler=_handler,
    output=ReferenceOutput,
    tags=("equity", "reference", "data"),
)
