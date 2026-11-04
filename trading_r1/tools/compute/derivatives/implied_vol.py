# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.derivatives.implied_vol: the volatility that reprices a quoted option (inverse BSM).

Solves BSM(σ) = market price for σ with SciPy's Brent root-finder. The BSM price is strictly
increasing in σ, so a root exists and is unique exactly when the price lies inside the
no-arbitrage band (call: [max(S*e^{−qT} − K*e^{−rT}, 0), S*e^{−qT}]; put:
[max(K*e^{−rT} − S*e^{−qT}, 0), K*e^{−rT}]) and within the volatility search range. Outside,
no implied volatility exists and the tool says why. BSM is European: a price that carries an
early-exercise premium (an American put, or a call on a dividend payer) implies a volatility
above the true one. Returns σ and the BSM Greeks at σ.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from scipy.optimize import brentq

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Fraction, Price
from trading_r1.tools.compute.derivatives.black_scholes import (
    GREEKS,
    Greeks,
    OptionType,
    black_scholes,
    bsm_price,
)

_DESCRIPTION = (
    "Implied volatility from a quoted option price: the σ at which Black-Scholes-Merton "
    "reprices it (European). Give the option price, spot, strike, years to expiry, "
    "continuously-compounded rate, option_type call|put and dividend_yield (default 0). "
    "Returns the implied volatility and the Greeks at it; errors if the price is outside "
    "the no-arbitrage band. European model: for an American option's price the result "
    "overstates volatility by the early-exercise premium."
)
# Search range for σ (decimal p.a.): effectively zero to 500%, the ceiling of quoted equity IV.
_VOL_MIN, _VOL_MAX = 1e-6, 5.0


class ImpliedVolRequest(BaseModel):
    """Input for implied volatility from a quoted price."""

    model_config = ConfigDict(allow_inf_nan=False)

    price: float = Field(..., gt=0, description="Quoted option price (e.g. the mid).")
    spot: float = Field(..., gt=0, description="Current underlying price S.")
    strike: float = Field(..., gt=0, description="Strike price K.")
    years: float = Field(..., gt=0, le=30, description="Time to expiry in years.")
    rate: float = Field(..., ge=-0.1, le=1, description="Continuously-compounded rate, p.a.")
    option_type: OptionType = Field("call", description="call or put.")
    dividend_yield: float = Field(0.0, ge=0, le=1, description="Continuous dividend yield q.")


class ImpliedVolOutput(Output):
    implied_volatility: Fraction
    price: Price
    no_arbitrage_band: list[Price]  # [floor, ceiling]
    greeks: Greeks  # Black-Scholes-Merton at the implied volatility
    option_type: Code


def no_arbitrage_band(req: ImpliedVolRequest) -> tuple[float, float]:
    fwd = req.spot * math.exp(-req.dividend_yield * req.years)
    pv_k = req.strike * math.exp(-req.rate * req.years)
    return (
        (max(fwd - pv_k, 0.0), fwd) if req.option_type == "call" else (max(pv_k - fwd, 0.0), pv_k)
    )


def compute_implied_vol(req: ImpliedVolRequest) -> dict[str, Any]:
    lo, hi = no_arbitrage_band(req)
    if req.price <= lo:
        raise ToolError(
            f"price {req.price:.4f} has no time value over the no-arbitrage floor {lo:.4f} "
            "(the discounted intrinsic value), so no implied volatility exists; a deep "
            "in-the-money quote at intrinsic, or a stale/bid quote, does this"
        )
    if req.price >= hi:
        raise ToolError(
            f"price {req.price:.4f} is at or above the no-arbitrage ceiling {hi:.4f}; no "
            "implied volatility exists: check the quote, spot, strike, expiry or rate"
        )

    def gap(vol: float) -> float:
        return (
            bsm_price(
                req.spot,
                req.strike,
                req.years,
                req.rate,
                vol,
                option_type=req.option_type,
                dividend_yield=req.dividend_yield,
            )
            - req.price
        )

    if gap(_VOL_MAX) < 0:
        raise ToolError(f"the price implies a volatility above {_VOL_MAX:g} (500% p.a.)")
    if gap(_VOL_MIN) > 0:
        raise ToolError(f"the price implies a volatility below {_VOL_MIN:g} (decimal p.a.)")
    vol = brentq(gap, _VOL_MIN, _VOL_MAX, xtol=1e-12)
    greeks = black_scholes(
        req.spot,
        req.strike,
        req.years,
        req.rate,
        vol,
        option_type=req.option_type,
        dividend_yield=req.dividend_yield,
    )
    return {
        "implied_volatility": vol,
        "price": req.price,
        "no_arbitrage_band": [lo, hi],
        "greeks": {k: greeks[k] for k in GREEKS},
        "option_type": req.option_type,
    }


async def _handler(req: ImpliedVolRequest) -> Result:
    return Result(ImpliedVolOutput(**compute_implied_vol(req)))


SPEC = ToolSpec(
    name="compute_derivatives_implied_vol",
    display_name="compute.derivatives.implied_vol",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=ImpliedVolRequest,
    handler=_handler,
    output=ImpliedVolOutput,
    tags=("compute", "derivatives", "options", "implied-volatility"),
)
