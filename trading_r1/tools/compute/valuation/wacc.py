# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.valuation.wacc: CAPM cost of equity + optional WACC blend.

The discount-rate input to the valuation pipeline (wacc -> dcf -> dcf_sensitivity).
Pure-math. CAPM only (risk_free_rate, equity_risk_premium, beta) gives the cost of equity
(WACC = Re). The debt trio (cost_of_debt, tax_rate, debt_to_equity, all-or-none)
adds the weighted blend. Re = Rf + β*ERP; WACC = E/V*Re + D/V*Rd*(1−Tc) with weights
from D/E. Exposes cost_of_equity + wacc as top-level fields for chaining/sensitivity,
plus implied_equity_risk_premium and wacc_premium_over_rf.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolSpec
from trading_r1.contract.units import Code, Fraction, Number

_DESCRIPTION = (
    "Cost of capital via CAPM + optional WACC blend: the discount rate for DCF. "
    "Always: risk_free_rate, equity_risk_premium, beta (CAPM cost of equity). "
    "Optionally cost_of_debt, tax_rate, debt_to_equity (all-or-none) for a WACC blend "
    "with after-tax cost of debt. β can come from data.equity.reference; the "
    "risk-free rate from data.market.rates. Returns cost_of_equity and wacc as "
    "top-level fields plus implied_equity_risk_premium and wacc_premium_over_rf. "
    "Pass rates as decimals (0.045 not 4.5)."
)


class CostOfCapitalRequest(BaseModel):
    """Input for CAPM cost of equity and optional WACC blend."""

    risk_free_rate: float = Field(
        ..., ge=0, lt=1, description="Annual risk-free rate, decimal (0.045)."
    )
    equity_risk_premium: float = Field(
        ..., ge=0, lt=1, description="Equity risk premium (Rm−Rf), decimal."
    )
    beta: float = Field(
        ..., ge=-2, le=5, description="Equity beta. From data.equity.reference or supplied."
    )
    cost_of_debt: float | None = Field(
        None, ge=0, lt=1, description="Pre-tax cost of debt, decimal (all-or-none)."
    )
    tax_rate: float | None = Field(
        None, ge=0, le=1, description="Effective tax rate, decimal (all-or-none)."
    )
    debt_to_equity: float | None = Field(None, ge=0, description="D/E ratio (all-or-none).")

    @model_validator(mode="after")
    def _check_debt_all_or_none(self) -> Self:
        set_count = sum(
            1 for f in (self.cost_of_debt, self.tax_rate, self.debt_to_equity) if f is not None
        )
        if 0 < set_count < 3:
            raise ValueError(
                "cost_of_debt, tax_rate, and debt_to_equity must all be set (WACC blend) "
                f"or all unset (CAPM only). Got {set_count}/3."
            )
        return self


class Components(BaseModel):
    risk_free_rate: Fraction
    equity_risk_premium: Fraction
    beta: Number
    # the WACC blend's inputs and weights, when debt is given
    cost_of_debt: Fraction = None
    tax_rate: Fraction = None
    debt_to_equity: Number = None
    equity_weight: Fraction = None
    debt_weight: Fraction = None
    after_tax_cost_of_debt: Fraction = None


class CostOfCapitalOutput(Output):
    cost_of_equity: Fraction
    wacc: Fraction
    implied_equity_risk_premium: Fraction
    wacc_premium_over_rf: Fraction
    method: Code
    components: Components


def compute_cost_of_capital(req: CostOfCapitalRequest) -> dict[str, Any]:
    """Pure-math core (Pydantic enforced ranges + all-or-none)."""
    rf, erp, beta = req.risk_free_rate, req.equity_risk_premium, req.beta
    cost_of_equity = rf + beta * erp
    components: dict[str, Any] = {"risk_free_rate": rf, "equity_risk_premium": erp, "beta": beta}

    if req.cost_of_debt is not None:
        rd, tc, de = req.cost_of_debt, req.tax_rate, req.debt_to_equity
        equity_weight = 1.0 / (1.0 + de)
        debt_weight = de / (1.0 + de)
        after_tax_cost_of_debt = rd * (1.0 - tc)
        wacc = equity_weight * cost_of_equity + debt_weight * after_tax_cost_of_debt
        components.update(
            {
                "cost_of_debt": rd,
                "tax_rate": tc,
                "debt_to_equity": de,
                "equity_weight": equity_weight,
                "debt_weight": debt_weight,
                "after_tax_cost_of_debt": after_tax_cost_of_debt,
            }
        )
        method = "CAPM + WACC"
    else:
        wacc = cost_of_equity
        method = "CAPM only"

    return {
        "cost_of_equity": cost_of_equity,
        "wacc": wacc,
        "implied_equity_risk_premium": cost_of_equity - rf,
        "wacc_premium_over_rf": wacc - rf,
        "method": method,
        "components": components,
    }


async def _handler(req: CostOfCapitalRequest) -> Result:
    res = compute_cost_of_capital(req)
    return Result(CostOfCapitalOutput(**res), Provenance(method=res["method"]))


SPEC = ToolSpec(
    name="compute_valuation_wacc",
    display_name="compute.valuation.wacc",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CostOfCapitalRequest,
    handler=_handler,
    output=CostOfCapitalOutput,
    tags=("compute", "valuation", "wacc", "capm", "cost-of-capital"),
)
