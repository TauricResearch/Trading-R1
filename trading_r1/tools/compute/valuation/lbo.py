# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.valuation.lbo: leveraged-buyout returns: IRR, MOIC and the value-creation bridge.

Entry: the price (EBITDA × entry multiple) plus entry fees and the minimum cash left in the
business are funded by debt and sponsor equity, so equity = EV + fees + minimum cash − debt.
Each year: interest on opening debt (charged on the opening balance so the sweep is not
circular), less its tax shield, capped at the tax on EBIT when an ``ebit_schedule`` is given,
since interest shields only taxable profit; the cash left after debt service is swept to repay
debt at ``cash_sweep`` (the rest, and anything once debt is gone, builds cash above the
minimum). The cash flows are unlevered (FCFF: chain `prev:<id>.fcff_schedule` and
`prev:<id>.ebit_schedule` from compute.modeling.three_statement), so interest is charged once,
on this deal's debt. Exit: EV at exit EBITDA × exit multiple, less debt, plus cash. Equity
proceeds are floored at zero (limited liability): a wiped-out deal returns MOIC 0, IRR −100%.
With a single entry and exit and no interim distributions, IRR = MOIC^(1/years) − 1 exactly.
The equity gain splits exactly into EBITDA growth (entry multiple × ΔEBITDA), multiple change
(exit EBITDA × Δmultiple), deleveraging (debt repaid + cash built above the minimum) and fees.
Cash falling below zero is a funding gap and is reported. Numbers only, no deal verdict.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count, Fraction, Money, Multiple, Text

_DESCRIPTION = (
    "Leveraged-buyout returns. Give entry_ebitda, entry_multiple, entry_debt, fees (entry costs "
    "funded by equity, default 0) and minimum_cash (left in the business at close, default 0); "
    "the unlevered cash flows (fcff_schedule: chain `prev:<id>.fcff_schedule`, and "
    "`prev:<id>.ebit_schedule` to cap the interest tax shield, from "
    "compute.modeling.three_statement), cost_of_debt, tax_rate and cash_sweep (share of cash "
    "after interest used to repay debt, default 1); exit_ebitda and exit_multiple. Returns the "
    "debt schedule, entry and exit equity, MOIC, IRR, leverage, and the value-creation bridge "
    "(EBITDA growth, multiple change, deleveraging, fees)."
)


class LBORequest(BaseModel):
    """Input for a leveraged-buyout returns model."""

    model_config = ConfigDict(allow_inf_nan=False)

    entry_ebitda: float = Field(..., gt=0, description="EBITDA at entry.")
    entry_multiple: float = Field(..., gt=0, le=50, description="Entry EV / EBITDA.")
    entry_debt: float = Field(..., ge=0, description="Acquisition debt.")
    fees: float = Field(0.0, ge=0, description="Entry costs funded by equity (fees, expenses).")
    minimum_cash: float = Field(0.0, ge=0, description="Cash left in the business at close.")
    fcff_schedule: list[float] = Field(
        ..., min_length=1, max_length=10, description="Unlevered FCF per hold year."
    )
    ebit_schedule: list[float] | None = Field(
        None, description="EBIT per hold year, to cap the interest tax shield (optional)."
    )
    cost_of_debt: float = Field(..., ge=0, lt=1, description="Pre-tax cost of the deal debt.")
    tax_rate: float = Field(..., ge=0, le=1, description="Tax rate for the interest shield.")
    cash_sweep: float = Field(1.0, ge=0, le=1, description="Share of cash after interest repaid.")
    exit_ebitda: float = Field(..., gt=0, description="EBITDA at exit.")
    exit_multiple: float = Field(..., gt=0, le=50, description="Exit EV / EBITDA.")

    @model_validator(mode="after")
    def _check(self) -> LBORequest:
        if self.ebit_schedule is not None and len(self.ebit_schedule) != len(self.fcff_schedule):
            raise ValueError("ebit_schedule must have one value per fcff_schedule year")
        uses = self.entry_ebitda * self.entry_multiple + self.fees + self.minimum_cash
        if self.entry_debt >= uses:
            raise ValueError(
                f"entry_debt {self.entry_debt:g} covers all of the uses ({uses:g}): the sponsor "
                "puts in no equity, so returns are undefined"
            )
        return self


class DebtYear(BaseModel):
    year: Count
    opening_debt: Money
    interest: Money
    tax_shield: Money
    fcff: Money
    cash_after_debt_service: Money
    repayment: Money
    closing_debt: Money
    cash: Money


class Bridge(BaseModel):
    """The equity gain, split exactly: the parts sum to ``equity_gain``."""

    ebitda_growth: Money
    multiple_change: Money
    deleveraging: Money
    fees: Money
    equity_gain: Money


class LBOOutput(Output):
    hold_years: Count
    entry_ev: Money
    entry_equity: Money
    entry_leverage: Multiple
    exit_ev: Money
    exit_debt: Money
    exit_cash: Money
    exit_equity: Money
    exit_leverage: Multiple
    moic: Multiple
    irr: Fraction
    min_cash: Money
    value_creation_bridge: Bridge
    debt_schedule: list[DebtYear]
    notes: list[Text] = []  # a wiped-out equity, a funding gap


def compute_lbo(req: LBORequest) -> dict[str, Any]:
    n = len(req.fcff_schedule)
    entry_ev = req.entry_ebitda * req.entry_multiple
    entry_equity = entry_ev + req.fees + req.minimum_cash - req.entry_debt
    debt, cash = req.entry_debt, req.minimum_cash
    schedule = []
    for i, fcff in enumerate(req.fcff_schedule):
        interest = debt * req.cost_of_debt
        shielded = interest
        if req.ebit_schedule is not None:
            shielded = min(interest, max(req.ebit_schedule[i], 0.0))
        after_service = fcff - interest + shielded * req.tax_rate
        repay = min(max(after_service, 0.0) * req.cash_sweep, debt)
        cash += after_service - repay
        schedule.append(
            {
                "year": i + 1,
                "opening_debt": debt,
                "interest": interest,
                "tax_shield": shielded * req.tax_rate,
                "fcff": fcff,
                "cash_after_debt_service": after_service,
                "repayment": repay,
                "closing_debt": debt - repay,
                "cash": cash,
            }
        )
        debt -= repay
    exit_ev = req.exit_ebitda * req.exit_multiple
    exit_equity = exit_ev - debt + cash
    proceeds = max(exit_equity, 0.0)  # limited liability
    moic = proceeds / entry_equity
    bridge = {
        "ebitda_growth": req.entry_multiple * (req.exit_ebitda - req.entry_ebitda),
        "multiple_change": req.exit_ebitda * (req.exit_multiple - req.entry_multiple),
        "deleveraging": (req.entry_debt - debt) + (cash - req.minimum_cash),
        "fees": -req.fees,
    }
    min_cash = min(s["cash"] for s in schedule)
    notes = []
    if exit_equity < 0:
        notes.append("Exit equity is negative: the equity is wiped out (proceeds floored at 0).")
    if min_cash < 0:
        notes.append("Cash goes below zero during the hold (min_cash): a funding gap.")
    return {
        "entry_ev": entry_ev,
        "entry_equity": entry_equity,
        "entry_leverage": req.entry_debt / req.entry_ebitda,
        "debt_schedule": schedule,
        "exit_ev": exit_ev,
        "exit_debt": debt,
        "exit_cash": cash,
        "exit_equity": exit_equity,
        "exit_leverage": debt / req.exit_ebitda,
        "moic": moic,
        "irr": moic ** (1 / n) - 1,
        "value_creation_bridge": {**bridge, "equity_gain": exit_equity - entry_equity},
        "min_cash": min_cash,
        "hold_years": n,
        "notes": notes,
    }


async def _handler(req: LBORequest) -> Result:
    return Result(LBOOutput(**compute_lbo(req)))


SPEC = ToolSpec(
    name="compute_valuation_lbo",
    display_name="compute.valuation.lbo",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=LBORequest,
    handler=_handler,
    output=LBOOutput,
    tags=("compute", "valuation", "lbo", "private-equity", "irr"),
)
