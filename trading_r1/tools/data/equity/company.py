# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""One company's valuation inputs from Tauric: the profile (size, ratios, balance-sheet
snapshot), the live quote, and trailing-twelve-month revenue, EBITDA and EPS from the filings.

The ratio, reference and comps tools read the same `Company`, so a multiple has one
definition: enterprise value (the profile's) over TTM EBITDA or revenue, the profile's P/E.

An ADR's statements can be in its home currency while it lists in dollars: the profile states
`financial_currency`, and for such a company the statement amounts (enterprise value, debt,
cash, sales) are not combined with the price, and the company says so in `notes`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from trading_r1.contract.spec import ToolError
from trading_r1.tools.data.equity import snapshot
from trading_r1.tools.data.tauric import NOT_PUBLISHED, Tauric


@dataclass(frozen=True)
class Company:
    ticker: str
    profile: dict[str, Any]
    quote: dict[str, Any]
    ttm: dict[str, Any] = field(default_factory=dict)  # revenue, ebitda, … (empty: no filings)
    filings_failure: str | None = None  # why the filings could not be read

    @property
    def name(self) -> str | None:
        return self.profile.get("instrument", {}).get("name")

    @property
    def currency(self) -> str | None:
        return self.profile.get("instrument", {}).get("currency")

    @property
    def kind(self) -> str | None:
        cls = self.profile.get("instrument", {}).get("classification") or {}
        return cls.get("instrument_type")

    @property
    def price(self) -> float | None:
        return self.quote.get("price")

    @property
    def valuation_date(self) -> str | None:
        """The close the profile's market cap, enterprise value and price multiples rest on; the
        live price in the quote can be newer."""
        return self.profile.get("market_cap_price_date")

    @property
    def consistent(self) -> bool:
        """False when the company's statements are in another currency than its listing."""
        stated = self.profile.get("financial_currency")
        return stated is None or stated == self.currency

    @property
    def notes(self) -> list[str]:
        out = []
        if self.filings_failure:
            out.append(
                f"{self.ticker}: the filings could not be read ({self.filings_failure}), so the "
                "figures built on trailing revenue and EBITDA are left out; this says nothing "
                "about the company."
            )
        if not self.consistent:
            out.append(
                f"{self.ticker} reports in {self.profile.get('financial_currency')}, not its "
                f"listing currency {self.currency}: EV multiples, price/sales and net debt are "
                "left out."
            )
        return out

    @property
    def net_debt(self) -> float | None:
        debt, cash = self.profile.get("total_debt"), self.profile.get("total_cash")
        if not self.consistent:
            return None
        return snapshot.leverage(debt, cash, None).get("net_debt")

    def multiples(self) -> dict[str, float]:
        """P/E, EV/EBITDA, EV/revenue, P/S, each only where defined, positive and in the
        listing currency (a multiple on a loss or a negative EV is not a multiple)."""
        p, t = self.profile, self.ttm
        ev = p.get("enterprise_value")
        same_unit = self.consistent
        raw = {
            "pe": p.get("pe_ratio"),
            "ev_ebitda": ev / t["ebitda"] if same_unit and ev and t.get("ebitda") else None,
            "ev_rev": ev / t["revenue"] if same_unit and ev and t.get("revenue") else None,
            "ps": p.get("price_to_sales") if same_unit else None,
        }
        return {k: v for k, v in raw.items() if v is not None and v > 0}

    @property
    def eps(self) -> float | None:
        """Trailing EPS: the filings' diluted TTM, else the close-based price over P/E."""
        if self.ttm.get("diluted_eps") is not None:
            return self.ttm["diluted_eps"]
        cap, shares, pe = (
            self.profile.get(k) for k in ("market_cap", "shares_outstanding", "pe_ratio")
        )
        return cap / shares / pe if cap and shares and pe else None


async def load(api: Tauric, ticker: str, *, filings: bool = True) -> Company:
    """A company's inputs; `filings=False` skips the TTM facts (reference needs none). A ticker
    Tauric does not serve raises its refusal, which names the fix. A company with no filings is
    without TTM figures; a filings fetch that failed for another reason says so in `notes`."""
    profile, quote = await asyncio.gather(
        snapshot.profile(api, ticker), api.get(f"/securities/{ticker}/quote")
    )
    if not profile.get("instrument"):
        raise ToolError(f"no data for '{ticker}': check the ticker")
    ttm: dict[str, Any] = {}
    failure = None
    if filings:
        try:
            ttm = await snapshot.ttm_figures(api, ticker, full=False)
        except ToolError as err:
            if err.code not in NOT_PUBLISHED:
                failure = str(err)
    return Company(ticker, profile, quote, ttm, failure)
