# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.fixed_income.horizon_return: a bond's return over a horizon, by source.

Pure-math. A fixed-rate bond held for a horizon on the given curve, unchanged or shifted in
parallel, earns its coupons (and what they earn reinvested), and its price moves as it ages
and as its yield moves. The yield at purchase is the bond's YTM (from its price, from a given
yield, or the curve's at its maturity); its spread to the curve is held, and at the horizon
its yield is read off the curve at the shorter remaining maturity, plus the spread, plus any
shock. The price change splits exactly into:

- pull to par: the price change from aging at an unchanged yield;
- roll-down: from its yield moving down (or up) the curve as its maturity shortens;
- rate shock: from the parallel shift.

The horizon is a whole number of coupon periods, so every price is at a coupon date (no
accrued interest). Yields are read off the observed (par) curve by linear interpolation, flat
beyond its ends, as ``compute.fixed_income.yield_curve`` reads its benchmark, and restated from
the curve's compounding (Treasury par yields: semiannual) to the bond's, so yield and spread
share one convention. A maturity between coupon dates is taken to the nearest whole period, as
``compute.fixed_income.bond`` does, and reported. A horizon under a year is not annualized
(GIPS). Option-free fixed-rate bonds only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Fraction, Number, Price
from trading_r1.tools.compute.fixed_income.bond import cashflows, price_at, solve_ytm
from trading_r1.tools.compute.fixed_income.yield_curve import CurvePoint

_DESCRIPTION = (
    "A bond's return over a holding horizon, split by source: coupon income, reinvestment of "
    "coupons, pull to par (aging at an unchanged yield), roll-down (its yield moving along the "
    "curve as maturity shortens) and a parallel rate shock. Give the bond (coupon_rate, "
    "years_to_maturity, frequency, face; price or ytm, else it is priced on the curve), the "
    "curve (chain `prev:<id>.curve` from data.market.rates), horizon_years (whole coupon "
    "periods), and optionally shock_bp and reinvestment_rate. The spread to the curve is held. "
    "Returns each component per face and as a share of the purchase price, the horizon "
    "return (annualized only for a horizon of a year or more), and the yields used. "
    "Option-free fixed-rate bonds."
)
_BP = 1e-4


class HorizonReturnRequest(BaseModel):
    """Input for a bond's horizon return on a curve."""

    model_config = ConfigDict(allow_inf_nan=False)

    coupon_rate: float = Field(..., ge=0, le=1, description="Annual coupon rate, decimal (0.04).")
    years_to_maturity: float = Field(..., gt=0, le=100, description="Years to maturity now.")
    frequency: int = Field(2, ge=1, le=12, description="Coupon payments per year (default 2).")
    face: float = Field(100.0, gt=0, description="Face/par value (default 100).")
    price: float | None = Field(None, gt=0, description="Price per `face` now (optional).")
    ytm: float | None = Field(None, gt=-0.1, le=1, description="Yield to maturity now (optional).")
    curve: list[CurvePoint] = Field(
        ..., min_length=2, max_length=60, description="Observed yields: chain `prev:<id>.curve`."
    )
    horizon_years: float = Field(..., gt=0, le=50, description="Holding horizon, years.")
    curve_frequency: int = Field(
        2, ge=1, le=12, description="Compounding of the curve's yields (Treasury par: 2)."
    )
    shock_bp: float = Field(0.0, ge=-1000, le=1000, description="Parallel shift at the horizon.")
    reinvestment_rate: float | None = Field(
        None, gt=-0.1, le=1, description="Rate coupons earn until the horizon (none if omitted)."
    )

    @model_validator(mode="after")
    def _consistent(self) -> HorizonReturnRequest:
        if self.price is not None and self.ytm is not None:
            raise ValueError("give at most one of `price` or `ytm`")
        periods = self.horizon_years * self.frequency
        if abs(periods - round(periods)) > 1e-9:
            raise ValueError(
                f"horizon_years must be whole coupon periods (multiples of 1/{self.frequency})"
            )
        if round(periods) >= round(self.years_to_maturity * self.frequency):
            raise ValueError("the horizon must end before the bond matures")
        mats = [p.maturity for p in self.curve]
        if len(set(mats)) != len(mats):
            raise ValueError("duplicate maturities in curve")
        self.curve.sort(key=lambda p: p.maturity)
        return self


class Components(BaseModel):
    """The horizon return by source, per face."""

    coupon_income: Price
    reinvestment_income: Price
    pull_to_par: Price
    roll_down: Price
    rate_shock: Price


class ComponentShares(BaseModel):
    """The same sources as a share of the purchase price."""

    coupon_income: Fraction
    reinvestment_income: Fraction
    pull_to_par: Fraction
    roll_down: Fraction
    rate_shock: Fraction


class HorizonReturnOutput(Output):
    price_now: Price
    price_at_horizon: Price
    ytm_now: Fraction
    spread_to_curve_bp: Number  # held over the horizon
    yield_at_horizon: Fraction
    roll_yield_change_bp: Number
    components: Components
    components_pct_of_price: ComponentShares
    horizon_return: Fraction
    annualized_return: Fraction = None  # a horizon under a year is not annualized (GIPS)
    horizon_years: Number
    years_to_maturity_used: Number  # whole coupon periods
    shock_bp: Number


def compute_horizon_return(req: HorizonReturnRequest) -> dict[str, Any]:
    f, face = req.frequency, req.face
    n = max(1, round(req.years_to_maturity * f))
    h = round(req.horizon_years * f)
    coupon = req.coupon_rate * face / f
    mats = [p.maturity for p in req.curve]
    rates = [p.rate for p in req.curve]

    def curve_at(t: float) -> float:
        """The curve's yield at maturity t (linear, flat beyond the ends), restated at the
        bond's compounding so the bond's yield and its spread share one convention."""
        y = float(np.interp(t, mats, rates))
        g = req.curve_frequency
        return f * ((1 + y / g) ** (g / f) - 1) if g != f else y

    def price(periods: int, y: float) -> float:
        return price_at(cashflows(coupon, periods, face), y / f)

    now = cashflows(coupon, n, face)
    if req.ytm is not None:
        y0 = req.ytm
    elif req.price is not None:
        y0 = solve_ytm(now, req.price, f)
    else:
        y0 = curve_at(n / f)
    spread = y0 - curve_at(n / f)
    y_roll = curve_at((n - h) / f) + spread
    y_h = y_roll + req.shock_bp * _BP

    p0 = price(n, y0)
    aged = price(n - h, y0)
    rolled = price(n - h, y_roll)
    p_h = price(n - h, y_h)
    r = req.reinvestment_rate
    reinvest = sum(coupon * ((1 + r / f) ** (h - k) - 1) for k in range(1, h + 1)) if r else 0.0
    components = {
        "coupon_income": coupon * h,
        "reinvestment_income": reinvest,
        "pull_to_par": aged - p0,
        "roll_down": rolled - aged,
        "rate_shock": p_h - rolled,
    }
    total = sum(components.values())
    horizon_return = total / p0
    if not np.isfinite(horizon_return):
        raise ToolError("the horizon return overflowed: check the yields and the horizon")
    return {
        "price_now": p0,
        "price_at_horizon": p_h,
        "ytm_now": y0,
        "spread_to_curve_bp": spread / _BP,
        "yield_at_horizon": y_h,
        "roll_yield_change_bp": (y_roll - y0) / _BP,
        "components": components,
        "components_pct_of_price": {k: v / p0 for k, v in components.items()},
        "horizon_return": horizon_return,
        # GIPS: a return for less than a year is not annualized
        "annualized_return": (
            (1 + horizon_return) ** (1 / req.horizon_years) - 1 if req.horizon_years >= 1 else None
        ),
        "horizon_years": req.horizon_years,
        "years_to_maturity_used": n / f,  # whole coupon periods
        "shock_bp": req.shock_bp,
    }


async def _handler(req: HorizonReturnRequest) -> Result:
    return Result(HorizonReturnOutput(**compute_horizon_return(req)))


SPEC = ToolSpec(
    name="compute_fixed_income_horizon_return",
    display_name="compute.fixed_income.horizon_return",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=HorizonReturnRequest,
    handler=_handler,
    output=HorizonReturnOutput,
    tags=("compute", "fixed_income", "bond", "horizon", "roll-down", "carry"),
)
