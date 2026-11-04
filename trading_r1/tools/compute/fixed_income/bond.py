# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.fixed_income.bond: fixed-rate bond analytics (price, yield, risk).

Pure-math. From a bond's terms and either its YTM or its price, returns the full analytic
set: clean price, YTM (solved from price if that's what's given), current yield, Macaulay &
modified duration, convexity, DV01, and the ±100bp price change from the second-order
approximation. The output carries the premium/discount read, the
positive-convexity asymmetry, DV01 additivity, and the flag that analytic duration is
invalid for callables/MBS (use effective duration).

Analytic measures assume option-free, fixed-rate cash flows.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, rendered
from trading_r1.contract.units import Code, Count, Fraction, Number, Price
from trading_r1.tools.compute.cashflows import irr

_DESCRIPTION = (
    "Fixed-rate bond analytics. Give coupon_rate (annual decimal, e.g. 0.04), "
    "years_to_maturity, frequency (coupons/yr, default 2), face (default 100), and EITHER "
    "ytm OR price (per face). Returns clean price, YTM, current yield, Macaulay & modified "
    "duration, convexity, DV01, and the +/-100bp price change. Option-free fixed-rate bonds "
    "only: use effective duration (reprice under a curve shift) for callables/MBS/floaters."
)


class BondInputs(BaseModel):
    coupon_rate: Fraction
    years_to_maturity: Number
    frequency: Count
    face: Price


class BondOutput(Output):
    model_config = rendered(
        then="Option-free fixed-rate analytics: for a callable, an MBS or a floater use "
        "effective duration (reprice under a curve shift)."
    )

    price: Price  # per `face`
    ytm: Fraction
    current_yield: Fraction
    premium_discount: Code
    macaulay_duration: Number  # years
    modified_duration: Number
    convexity: Number
    dv01: Price  # price change per 1bp, per `face`
    price_change_up_100bp_pct: Fraction  # duration + convexity approximation
    price_change_down_100bp_pct: Fraction
    n_periods: Count
    coupon_per_period: Price
    periodic_yield: Fraction
    inputs: BondInputs


def cashflows(coupon_per_period: float, n: int, face: float) -> list[float]:
    cfs = [coupon_per_period] * n
    cfs[-1] += face
    return cfs


def price_at(cfs: list[float], periodic_yield: float) -> float:
    return sum(cf / (1 + periodic_yield) ** (t + 1) for t, cf in enumerate(cfs))


def solve_ytm(cfs: list[float], target_price: float, m: int) -> float:
    """The annual nominal yield (m periods a year) at which the bond's flows reprice to
    target_price: the IRR of the price paid and the flows received."""
    return irr([-target_price, *cfs]) * m


def bond_analytics(
    coupon_rate: float,
    years_to_maturity: float,
    *,
    frequency: int = 2,
    face: float = 100.0,
    ytm: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    """Price/yield + duration/convexity/DV01 for an option-free fixed-rate bond."""
    m = frequency
    n = max(1, round(years_to_maturity * m))  # a bond inside one period still pays once
    coupon_per_period = coupon_rate * face / m
    cfs = cashflows(coupon_per_period, n, face)

    if ytm is None:
        ytm = solve_ytm(cfs, float(price), m)  # type: ignore[arg-type]
    per_y = ytm / m
    # Always the model price at the solved yield (even when the user passed a price) so the
    # duration/convexity below are internally consistent with the yield.
    p = price_at(cfs, per_y)

    # Macaulay duration (years): PV-weighted average time to cash flow.
    pv = [cf / (1 + per_y) ** (t + 1) for t, cf in enumerate(cfs)]
    p_model = sum(pv)
    d_mac = sum((t + 1) / m * pvt for t, pvt in enumerate(pv)) / p_model
    d_mod = d_mac / (1 + per_y)
    # Convexity (annual): periods² measure scaled by m².
    conv_periods = sum((t + 1) * (t + 2) * pvt / (1 + per_y) ** 2 for t, pvt in enumerate(pv))
    conv_periods /= p_model
    convexity = conv_periods / (m * m)
    dv01 = d_mod * p * 0.0001  # currency change per 1bp, per `face`

    current_yield = coupon_rate * face / p
    up = -d_mod * 0.01 + 0.5 * convexity * 0.01**2  # +100bp
    down = d_mod * 0.01 + 0.5 * convexity * 0.01**2  # -100bp
    # Par first, with a tolerance: float noise (~1e-13) would read as a premium.
    par_tol = max(1e-6, face * 1e-9)
    label = "par" if abs(p - face) < par_tol else "premium" if p > face else "discount"

    return {
        "price": p,
        "ytm": ytm,
        "current_yield": current_yield,
        "premium_discount": label,
        "macaulay_duration": d_mac,
        "modified_duration": d_mod,
        "convexity": convexity,
        "dv01": dv01,
        "price_change_up_100bp_pct": up,
        "price_change_down_100bp_pct": down,
        "n_periods": n,
        "coupon_per_period": coupon_per_period,
        "periodic_yield": per_y,
        "inputs": {
            "coupon_rate": coupon_rate,
            "years_to_maturity": years_to_maturity,
            "frequency": m,
            "face": face,
        },
    }


class BondRequest(BaseModel):
    """Input for option-free fixed-rate bond analytics. Give exactly one of ytm or price."""

    model_config = ConfigDict(allow_inf_nan=False)  # reject NaN/inf inputs

    coupon_rate: float = Field(..., ge=0, le=1, description="Annual coupon rate, decimal (0.04).")
    years_to_maturity: float = Field(..., gt=0, le=100, description="Years to maturity.")
    frequency: int = Field(2, ge=1, le=12, description="Coupon payments per year (default 2).")
    face: float = Field(100.0, gt=0, description="Face/par value (default 100).")
    ytm: float | None = Field(None, gt=-0.1, le=1, description="Yield to maturity, decimal p.a.")
    price: float | None = Field(None, gt=0, description="Clean price per `face` (solve for YTM).")

    @model_validator(mode="after")
    def _one_of_ytm_price(self) -> BondRequest:
        if (self.ytm is None) == (self.price is None):
            raise ValueError("give exactly one of `ytm` or `price`")
        return self


async def _handler(req: BondRequest) -> Result:
    r = bond_analytics(
        req.coupon_rate,
        req.years_to_maturity,
        frequency=req.frequency,
        face=req.face,
        ytm=req.ytm,
        price=req.price,
    )
    return Result(BondOutput(**r))


SPEC = ToolSpec(
    name="compute_fixed_income_bond",
    display_name="compute.fixed_income.bond",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=BondRequest,
    handler=_handler,
    output=BondOutput,
    tags=("compute", "fixed-income", "bond", "duration", "convexity", "dv01"),
)
