# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.modeling.three_statement: a driver-based forward three-statement model.

From one year of summary historicals and driver assumptions (each a constant or a per-year
list), projects the income statement, cash-flow statement and balance sheet, and exposes the
schedules downstream tools chain:

- ``fcff_schedule``: unlevered free cash flow, EBIT*(1 − t) + D&A − capex − ΔNWC. Discount at
  WACC in compute.valuation.dcf (with net debt), or sweep in compute.valuation.lbo;
- ``fcfe_schedule``: free cash flow to equity, NI + D&A − capex − ΔNWC + net borrowing. Discount
  at the cost of equity (net debt 0). It equals FCFF − interest*(1 − t) + net borrowing while
  pre-tax income is positive; with EBT < 0 < EBIT the interest shield is unused (no loss
  carry-forward), so FCFE is lower than that identity gives;
- ``ebitda_schedule``, ``ebit_schedule`` (caps an LBO's interest tax shield), ``ending_debt``,
  ``ending_cash``.

Mechanics: interest on the average of opening and closing debt (debt is a driver, so there is no
circularity); tax on positive pre-tax income only (no loss carry-forward); PP&E rolls forward as
PP&E + capex − D&A; opening equity balances the opening balance sheet, and each year's balance
sheet residual is reported (zero when the statements tie). Cash can go negative, reported as a
funding gap. Units are the caller's; rates and intensities are decimals.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, hint
from trading_r1.contract.units import Money, Number

_DESCRIPTION = (
    "Forward three-statement model (income statement, cash flow, balance sheet) from one year "
    "of historicals and drivers, each driver a constant or a per-year list. Returns the "
    "statements plus chain-ready schedules: fcff_schedule (unlevered FCF, for a WACC DCF or an "
    "LBO), fcfe_schedule (FCF to equity, for a cost-of-equity DCF), ebitda_schedule, "
    "ending_debt, ending_cash, and the balance-sheet check. Units are yours; rates decimals."
)
# Each driver is a constant or a per-year list, with the same bounds either way.
Growth = Annotated[float, Field(gt=-1, le=10)]
Margin = Annotated[float, Field(ge=-1, le=1)]
Intensity = Annotated[float, Field(ge=0, le=2)]
_DRIVERS = (
    "revenue_growth",
    "gross_margin_path",
    "opex_path",
    "da_path",
    "capex_path",
    "working_capital_path",
    "debt_change",
)


def _path(v: float | list[float] | None, default: float, n: int) -> list[float]:
    if v is None:
        return [default] * n
    return list(v) if isinstance(v, list) else [float(v)] * n


class ThreeStatementRequest(BaseModel):
    """Input for a forward three-statement projection."""

    model_config = ConfigDict(allow_inf_nan=False)

    revenue: float = Field(..., gt=0, description="Last-year revenue (year 0).")
    gross_margin: float = Field(..., ge=-1, le=1, description="Last-year gross margin.")
    opex_pct_of_revenue: float = Field(
        ..., ge=0, le=2, description="Opex (above EBITDA) / revenue."
    )
    da_pct_of_revenue: float = Field(..., ge=0, le=1, description="D&A / revenue.")
    capex_pct_of_revenue: float = Field(..., ge=0, le=2, description="Capex / revenue.")
    working_capital_pct_of_revenue: float = Field(
        ..., ge=-1, le=1, description="Net working capital / revenue (negative = net liability)."
    )
    tax_rate: float = Field(..., ge=0, le=1, description="Effective tax rate.")
    opening_cash: float = Field(..., ge=0, description="Cash at the start of year 1.")
    opening_debt: float = Field(..., ge=0, description="Debt at the start of year 1.")
    opening_ppe: float = Field(..., ge=0, description="Net PP&E at the start of year 1.")
    cost_of_debt: float = Field(..., ge=0, le=1, description="Pre-tax cost of debt.")
    revenue_growth: Growth | list[Growth] = Field(..., description="Revenue growth per year.")
    gross_margin_path: Margin | list[Margin] | None = Field(
        None, description="Gross margin (default: held)."
    )
    opex_path: Intensity | list[Intensity] | None = Field(
        None, description="Opex / revenue (default: held)."
    )
    da_path: Intensity | list[Intensity] | None = Field(
        None, description="D&A / revenue (default: held)."
    )
    capex_path: Intensity | list[Intensity] | None = Field(
        None, description="Capex / revenue (default: held)."
    )
    working_capital_path: Margin | list[Margin] | None = Field(
        None, description="NWC / revenue (default: held)."
    )
    debt_change: float | list[float] = Field(
        0.0, description="Net borrowing per year (+ borrow, − repay)."
    )
    dividend_payout: float = Field(0.0, ge=0, le=1, description="Dividends / positive net income.")
    years: int = Field(5, ge=1, le=10, description="Projection years (default 5).")

    @model_validator(mode="after")
    def _lengths(self) -> ThreeStatementRequest:
        for name in _DRIVERS:
            v = getattr(self, name)
            if isinstance(v, list) and len(v) != self.years:
                raise ValueError(f"{name} has {len(v)} values for {self.years} years")
        debt = self.opening_debt
        for t, change in enumerate(_path(self.debt_change, 0.0, self.years), start=1):
            debt += change
            if debt < 0:
                raise ValueError(f"debt_change repays more than the debt outstanding in year {t}")
        return self


class Year(BaseModel):
    year: Number
    revenue: Money
    gross_profit: Money
    ebitda: Money
    ebit: Money
    interest: Money
    pretax_income: Money
    taxes: Money
    net_income: Money
    d_and_a: Money
    capex: Money
    change_in_nwc: Money
    operating_cash_flow: Money
    fcff: Money
    fcfe: Money
    dividends: Money
    cash: Money
    debt: Money
    ppe: Money
    working_capital: Money
    equity: Money
    balance_sheet_residual: Money


# The schedules repeat columns of `years`, for chaining: named, not shown.
Schedule = Annotated[list[Money], hint(hidden=True)]


class ThreeStatementOutput(Output):
    years: list[Year]
    fcff_schedule: Schedule
    fcfe_schedule: Schedule
    ebitda_schedule: Schedule
    ebit_schedule: Schedule
    ending_debt: Schedule
    ending_cash: Schedule
    min_cash: Money  # below zero: a funding gap
    max_balance_sheet_residual: Money  # zero when the statements tie


def compute_three_statement(req: ThreeStatementRequest) -> dict[str, Any]:
    n = req.years
    growth = _path(req.revenue_growth, 0.0, n)
    margin = _path(req.gross_margin_path, req.gross_margin, n)
    opex_pct = _path(req.opex_path, req.opex_pct_of_revenue, n)
    capex_pct = _path(req.capex_path, req.capex_pct_of_revenue, n)
    nwc_pct = _path(req.working_capital_path, req.working_capital_pct_of_revenue, n)
    borrow = _path(req.debt_change, 0.0, n)
    da_pct = _path(req.da_path, req.da_pct_of_revenue, n)

    revenue, cash, debt, ppe = req.revenue, req.opening_cash, req.opening_debt, req.opening_ppe
    nwc = req.revenue * req.working_capital_pct_of_revenue
    equity = cash + ppe + nwc - debt  # the opening balance sheet balances by construction
    years: list[dict[str, float]] = []
    for t in range(n):
        revenue *= 1 + growth[t]
        ebitda = revenue * (margin[t] - opex_pct[t])
        da = revenue * da_pct[t]
        ebit = ebitda - da
        closing_debt = debt + borrow[t]
        interest = (debt + closing_debt) / 2 * req.cost_of_debt
        ebt = ebit - interest
        taxes = max(ebt, 0.0) * req.tax_rate
        net_income = ebt - taxes
        capex = revenue * capex_pct[t]
        closing_nwc = revenue * nwc_pct[t]
        delta_nwc = closing_nwc - nwc
        fcff = ebit - max(ebit, 0.0) * req.tax_rate + da - capex - delta_nwc
        fcfe = net_income + da - capex - delta_nwc + borrow[t]
        dividends = max(net_income, 0.0) * req.dividend_payout
        cash += fcfe - dividends
        debt, nwc, ppe = closing_debt, closing_nwc, ppe + capex - da
        equity += net_income - dividends
        years.append(
            {
                "year": t + 1,
                "revenue": revenue,
                "gross_profit": revenue * margin[t],
                "ebitda": ebitda,
                "ebit": ebit,
                "interest": interest,
                "pretax_income": ebt,
                "taxes": taxes,
                "net_income": net_income,
                "d_and_a": da,
                "capex": capex,
                "change_in_nwc": delta_nwc,
                "operating_cash_flow": net_income + da - delta_nwc,
                "fcff": fcff,
                "fcfe": fcfe,
                "dividends": dividends,
                "cash": cash,
                "debt": debt,
                "ppe": ppe,
                "working_capital": nwc,
                "equity": equity,
                "balance_sheet_residual": (cash + ppe + nwc) - (debt + equity),
            }
        )
    return {
        "years": years,
        "fcff_schedule": [y["fcff"] for y in years],
        "fcfe_schedule": [y["fcfe"] for y in years],
        "ebitda_schedule": [y["ebitda"] for y in years],
        "ebit_schedule": [y["ebit"] for y in years],
        "ending_debt": [y["debt"] for y in years],
        "ending_cash": [y["cash"] for y in years],
        "min_cash": min(y["cash"] for y in years),
        "max_balance_sheet_residual": max(abs(y["balance_sheet_residual"]) for y in years),
    }


async def _handler(req: ThreeStatementRequest) -> Result:
    return Result(ThreeStatementOutput(**compute_three_statement(req)))


SPEC = ToolSpec(
    name="compute_modeling_three_statement",
    display_name="compute.modeling.three_statement",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=ThreeStatementRequest,
    handler=_handler,
    output=ThreeStatementOutput,
    tags=("compute", "modeling", "three-statement", "projection", "fcff"),
)
