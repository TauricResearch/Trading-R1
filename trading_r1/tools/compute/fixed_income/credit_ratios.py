# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.fixed_income.credit_ratios: the leverage, coverage and liquidity ratios of a borrower.

The fundamental lens on credit (the complement to compute.fixed_income.credit_spread's market
lens), for one period of actuals (data.equity.fundamentals) or a projected year (chain from
compute.modeling.three_statement): gross and net debt / EBITDA, debt / equity and debt /
capital; EBITDA, EBIT and (EBITDA − capex) interest coverage; FCF / debt and FFO / debt (FFO =
EBITDA − interest − cash taxes); the current ratio. A ratio is null where it is undefined:
leverage multiples on non-positive EBITDA, capital-structure ratios on non-positive book equity,
coverage without interest expense. Numbers only, no rating or investment-grade label.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Fraction, Money, Multiple

_DESCRIPTION = (
    "Credit ratio panel for one period. Give ebitda, interest_expense, total_debt and cash, and "
    "any of ebit, capex, fcf, cash_taxes, total_equity, current_assets, current_liabilities "
    "(from data.equity.fundamentals, or a projected year from compute.modeling.three_statement). "
    "Returns leverage (gross/net debt to EBITDA, debt to equity and to capital), coverage "
    "(EBITDA, EBIT and EBITDA−capex over interest, FCF/debt, FFO/debt) and the current ratio; "
    "undefined ratios are null."
)


class CreditRatiosRequest(BaseModel):
    """Input for a one-period credit ratio panel."""

    model_config = ConfigDict(allow_inf_nan=False)

    ebitda: float = Field(..., description="EBITDA for the period.")
    interest_expense: float = Field(..., ge=0, description="Interest expense for the period.")
    total_debt: float = Field(..., ge=0, description="Total debt at period end.")
    cash: float = Field(0.0, ge=0, description="Cash and equivalents at period end.")
    ebit: float | None = Field(None, description="EBIT (operating income).")
    capex: float | None = Field(None, description="Capital expenditure (magnitude).")
    fcf: float | None = Field(None, description="Free cash flow (operating cash flow − capex).")
    cash_taxes: float | None = Field(None, ge=0, description="Cash taxes paid (for FFO).")
    total_equity: float | None = Field(None, description="Book equity.")
    current_assets: float | None = Field(None, ge=0, description="Current assets.")
    current_liabilities: float | None = Field(None, ge=0, description="Current liabilities.")


class Leverage(BaseModel):
    gross_debt_to_ebitda: Multiple
    net_debt_to_ebitda: Multiple
    debt_to_equity: Multiple
    debt_to_capital: Fraction


class Coverage(BaseModel):
    ebitda_to_interest: Multiple
    ebit_to_interest: Multiple
    ebitda_minus_capex_to_interest: Multiple
    fcf_to_debt: Fraction
    ffo_to_debt: Fraction


class Liquidity(BaseModel):
    current_ratio: Multiple


class CreditRatiosOutput(Output):
    """A ratio is null (n/a) where it is undefined or its inputs were not given."""

    net_debt: Money
    leverage: Leverage
    coverage: Coverage
    liquidity: Liquidity


def _ratio(a: float | None, b: float | None, *, need_positive: bool = True) -> float | None:
    if a is None or b is None or (b <= 0 if need_positive else b == 0):
        return None
    return a / b


def compute_credit_ratios(req: CreditRatiosRequest) -> dict[str, Any]:
    debt, ebitda, interest = req.total_debt, req.ebitda, req.interest_expense
    net_debt = debt - req.cash
    equity = req.total_equity if req.total_equity is not None and req.total_equity > 0 else None
    capex = abs(req.capex) if req.capex is not None else None
    ffo = ebitda - interest - req.cash_taxes if req.cash_taxes is not None else None
    return {
        "net_debt": net_debt,
        "leverage": {
            "gross_debt_to_ebitda": _ratio(debt, ebitda),
            "net_debt_to_ebitda": _ratio(net_debt, ebitda),
            "debt_to_equity": _ratio(debt, equity),
            "debt_to_capital": _ratio(debt, debt + equity) if equity is not None else None,
        },
        "coverage": {
            "ebitda_to_interest": _ratio(ebitda, interest),
            "ebit_to_interest": _ratio(req.ebit, interest),
            "ebitda_minus_capex_to_interest": (
                _ratio(ebitda - capex, interest) if capex is not None else None
            ),
            "fcf_to_debt": _ratio(req.fcf, debt),
            "ffo_to_debt": _ratio(ffo, debt),
        },
        "liquidity": {"current_ratio": _ratio(req.current_assets, req.current_liabilities)},
    }


async def _handler(req: CreditRatiosRequest) -> Result:
    return Result(CreditRatiosOutput(**compute_credit_ratios(req)))


SPEC = ToolSpec(
    name="compute_fixed_income_credit_ratios",
    display_name="compute.fixed_income.credit_ratios",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CreditRatiosRequest,
    handler=_handler,
    output=CreditRatiosOutput,
    tags=("compute", "fixed_income", "credit", "leverage", "coverage"),
)
