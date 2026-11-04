# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.derivatives.position_greeks: net Greeks and cost of a multi-leg option position.

A strategy (spread, straddle, collar, covered call, …) is several legs, possibly with stock.
Each leg is priced with Black-Scholes-Merton
(`compute.derivatives.black_scholes`), its Greeks scaled by signed contracts × the contract
multiplier, and summed, with an optional underlying share position (delta 1 per share). Signs:
quantity > 0 long, < 0 short; net cost > 0 is a debit paid, < 0 a credit received. Greeks are in
whole-position units (vega per vol point, theta per day, rho per 1%). European per-leg Greeks,
the practitioner convention for position risk. Aggregated numbers only, no strategy labels.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Money, Number, Price
from trading_r1.tools.compute.derivatives.black_scholes import (
    GREEKS,
    Greeks,
    OptionType,
    black_scholes,
)

_DESCRIPTION = (
    "Net Greeks and cost of a multi-leg option position (spreads, straddles, collars, covered "
    "calls). Give spot, continuously-compounded rate, dividend_yield (default 0), the legs "
    "(each: signed quantity in contracts, +long / −short, option_type, strike, years, vol) and "
    "optionally underlying_shares (signed). Returns each leg's price and cost and the "
    "position's net delta, gamma, vega (per vol point), theta (per day), rho (per 1%) and net "
    "cost (+ debit / − credit). contract_multiplier default 100 (US listed options). Use it "
    "for any multi-leg or stock-plus-option position so the net exposure is exact."
)


class OptionLeg(BaseModel):
    """One option leg of a position."""

    model_config = ConfigDict(allow_inf_nan=False)

    quantity: float = Field(..., description="Signed contracts: > 0 long, < 0 short.")
    option_type: OptionType = Field(..., description="call or put.")
    strike: float = Field(..., gt=0, description="Strike price K.")
    years: float = Field(..., gt=0, le=30, description="Time to expiry in years.")
    vol: float = Field(..., gt=0, le=5, description="Volatility, decimal p.a.")


class PositionGreeksRequest(BaseModel):
    """Input for a multi-leg option position."""

    model_config = ConfigDict(allow_inf_nan=False)

    spot: float = Field(..., gt=0, description="Current underlying price S.")
    rate: float = Field(..., ge=-0.1, le=1, description="Continuously-compounded rate, p.a.")
    legs: list[OptionLeg] = Field(..., min_length=1, max_length=20)
    dividend_yield: float = Field(0.0, ge=0, le=1, description="Continuous dividend yield q.")
    underlying_shares: float = Field(0.0, description="Signed shares held with the options.")
    contract_multiplier: float = Field(100.0, gt=0, description="Shares per contract.")


class LegResult(BaseModel):
    quantity: Count
    option_type: Code
    strike: Price
    years: Number
    vol: Fraction
    price: Price
    cost: Money
    greeks: Greeks  # whole-leg units


class PositionGreeksOutput(Output):
    net_greeks: Greeks
    net_cost: Money  # > 0 a debit paid, < 0 a credit received
    legs: list[LegResult]
    underlying_shares: Count
    contract_multiplier: Count
    spot: Price


def compute_position_greeks(req: PositionGreeksRequest) -> dict[str, Any]:
    net = dict.fromkeys(GREEKS, 0.0)
    net["delta"] = req.underlying_shares  # stock: delta 1 per share, no other Greeks
    cost = req.underlying_shares * req.spot
    legs = []
    for leg in req.legs:
        bs = black_scholes(
            req.spot,
            leg.strike,
            leg.years,
            req.rate,
            leg.vol,
            option_type=leg.option_type,
            dividend_yield=req.dividend_yield,
        )
        scale = leg.quantity * req.contract_multiplier
        leg_greeks = {g: bs[g] * scale for g in GREEKS}  # whole-leg units
        for g in GREEKS:
            net[g] += leg_greeks[g]
        leg_cost = bs["price"] * scale
        cost += leg_cost
        legs.append(
            {**leg.model_dump(), "price": bs["price"], "cost": leg_cost, "greeks": leg_greeks}
        )
    return {
        "net_greeks": net,
        "net_cost": cost,
        "legs": legs,
        "underlying_shares": req.underlying_shares,
        "contract_multiplier": req.contract_multiplier,
        "spot": req.spot,
    }


async def _handler(req: PositionGreeksRequest) -> Result:
    return Result(PositionGreeksOutput(**compute_position_greeks(req)))


SPEC = ToolSpec(
    name="compute_derivatives_position_greeks",
    display_name="compute.derivatives.position_greeks",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=PositionGreeksRequest,
    handler=_handler,
    output=PositionGreeksOutput,
    tags=("compute", "derivatives", "options", "greeks", "strategy"),
)
