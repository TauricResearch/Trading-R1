# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The equity snapshot every valuation-side tool shares: Tauric's profile (ratios, size,
classification), the analyst consensus, and trailing-twelve-month figures from the filings.

One place for what fundamentals, ratios, comps and reference all need, so a figure has one
definition. Profile ratios are as Tauric publishes them (fractions: margins, growth, ROE, yield);
TTM flows come from the SEC facts (`statements.py`) and are in the reporting currency.
"""

from __future__ import annotations

import asyncio
from typing import Any

from trading_r1.contract.spec import ToolError
from trading_r1.tools.data.equity import statements
from trading_r1.tools.data.tauric import Tauric

# the flows each tool's TTM figures are built from
INCOME_LINES = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "ebitda",
    "diluted_eps",
)
CASH_LINES = ("operating_cash_flow", "capital_expenditure", "free_cash_flow")
_LITE_LINES = ("revenue", "ebitda", "diluted_eps")
# Enough facts for the TTM at any point of the fiscal year: a year, the cumulatives of this and
# the last fiscal year (up to three each), and the quarters a first-quarter TTM needs: back to
# last year's first quarter, five quarterly facts since Tauric derives Q4 from the 10-K, and one
# more for a restated quarter.
_TTM_PERIODS = {"annual": statements.years(2), "ytd": 4, "quarterly": 6}


async def profile(api: Tauric, ticker: str) -> dict[str, Any]:
    return await api.get(f"/securities/{ticker}/profile")


async def analyst(api: Tauric, ticker: str) -> dict[str, Any] | None:
    """The consensus, or None where the vendor publishes none (funds, indices)."""
    try:
        return await api.get(f"/securities/{ticker}/analyst")
    except ToolError as err:
        if err.code == "not_published":
            return None
        raise


async def ttm_figures(api: Tauric, ticker: str, *, full: bool = True) -> dict[str, Any]:
    """Trailing-twelve-month figures from the filings (`statements.py`), with `currency`,
    `as_of` and `basis` (how each figure was found: a filed concept, or a rule such as
    EBITDA's). `full` adds cash flow and margins' inputs; without it only revenue, EBITDA and
    EPS (what multiples need). Empty for a filer without US-GAAP facts."""
    income_keys = INCOME_LINES if full else _LITE_LINES
    jobs = [statements.load_facts(api, ticker, "income", _TTM_PERIODS, income_keys)]
    if full:
        jobs.append(statements.load_facts(api, ticker, "cashflow", _TTM_PERIODS, CASH_LINES))
    income, *rest = await asyncio.gather(*jobs)
    out, basis, as_of = statements.ttm_lines(income, "income", income_keys)
    if rest:
        cash, cash_basis, _ = statements.ttm_lines(rest[0], "cashflow", CASH_LINES)
        out |= cash
        basis |= cash_basis
    if out:
        out["currency"] = statements.currency(income["annual"] | income["ytd"])
        out["as_of"] = as_of
        out["basis"] = basis
    return out


def leverage(
    total_debt: float | None, total_cash: float | None, ebitda: float | None
) -> dict[str, float]:
    """`net_debt` (debt less cash) and, when EBITDA is positive, `net_debt_to_ebitda`."""
    if total_debt is None or total_cash is None:
        return {}
    out = {"net_debt": total_debt - total_cash}
    if ebitda and ebitda > 0:
        out["net_debt_to_ebitda"] = out["net_debt"] / ebitda
    return out
