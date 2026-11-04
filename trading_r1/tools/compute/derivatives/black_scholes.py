# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.derivatives.black_scholes: European option price + Greeks (BSM).

Pure-math. Prices a European call/put on an asset with a continuous dividend yield via
Black-Scholes-Merton, and returns the first-order Greeks in the conventions the desk uses
(vega per volatility point, theta per calendar day, rho per 1% rate move). N(d2) is the
risk-neutral ITM probability (a pricing weight, not a real-world probability), the model
is European-only, and sigma is forward-looking implied vol.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec, rendered
from trading_r1.contract.units import Code, Fraction, Number, Price

_DESCRIPTION = (
    "Black-Scholes-Merton price and Greeks for a European option. Give spot, strike, "
    "years to expiry, the continuously-compounded risk-free rate, and volatility (both "
    "decimals p.a.); option_type call|put, dividend_yield default 0. Returns price, delta, "
    "gamma, vega (per vol point), theta (per day), rho (per 1% rate), and the risk-neutral "
    "in-the-money probability. European exercise only: use a lattice for American options."
)

_N = NormalDist()


def _d1_d2(
    spot: float, strike: float, years: float, rate: float, vol: float, dividend_yield: float
) -> tuple[float, float]:
    sqrt_t = years**0.5
    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * years) / (
        vol * sqrt_t
    )
    return d1, d1 - vol * sqrt_t


OptionType = Literal["call", "put"]
# The first-order Greeks every derivatives tool reports, in black_scholes' units.
GREEKS = ("delta", "gamma", "vega_per_point", "theta_per_day", "rho_per_1pct")


class Greeks(BaseModel):
    """First-order Greeks: vega per vol point, theta per calendar day, rho per 1% rate."""

    delta: Number
    gamma: Number
    vega_per_point: Number
    theta_per_day: Number
    rho_per_1pct: Number


class OptionInputs(BaseModel):
    spot: Price
    strike: Price
    years: Number
    rate: Fraction
    vol: Fraction
    dividend_yield: Fraction


class BlackScholesOutput(Output):
    model_config = rendered(
        then="N(d2) is a risk-neutral pricing weight, not a real-world probability. European "
        "exercise only: an American option needs compute.derivatives.binomial."
    )

    option_type: Code
    price: Price
    intrinsic_value: Price
    time_value: Price
    delta: Number
    gamma: Number
    vega_per_point: Number
    theta_per_day: Number
    rho_per_1pct: Number
    prob_itm_risk_neutral: Fraction  # a pricing weight, not a real-world probability
    d1: Number
    d2: Number
    inputs: OptionInputs


def bsm_price(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    vol: float,
    *,
    option_type: OptionType = "call",
    dividend_yield: float = 0.0,
) -> float:
    """Unrounded European BSM price: the continuous function a root-finder needs."""
    d1, d2 = _d1_d2(spot, strike, years, rate, vol, dividend_yield)
    fwd = spot * math.exp(-dividend_yield * years)
    pv_k = strike * math.exp(-rate * years)
    if option_type == "call":
        return fwd * _N.cdf(d1) - pv_k * _N.cdf(d2)
    return pv_k * _N.cdf(-d2) - fwd * _N.cdf(-d1)


def black_scholes(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    vol: float,
    *,
    option_type: OptionType = "call",
    dividend_yield: float = 0.0,
) -> dict[str, Any]:
    """European BSM price + first-order Greeks. Rates/vol continuously compounded, decimals."""
    is_call = option_type == "call"
    sqrt_t = years**0.5
    d1, d2 = _d1_d2(spot, strike, years, rate, vol, dividend_yield)
    disc_q = math.exp(-dividend_yield * years)
    disc_r = math.exp(-rate * years)
    nd1, nd2 = _N.cdf(d1), _N.cdf(d2)
    pdf_d1 = _N.pdf(d1)
    price = bsm_price(
        spot, strike, years, rate, vol, option_type=option_type, dividend_yield=dividend_yield
    )

    if is_call:
        delta = disc_q * nd1
        # annual theta, then per calendar day
        theta_annual = (
            -(spot * disc_q * pdf_d1 * vol) / (2 * sqrt_t)
            - rate * strike * disc_r * nd2
            + dividend_yield * spot * disc_q * nd1
        )
        rho = strike * years * disc_r * nd2
        prob_itm_rn = nd2  # risk-neutral P(S_T > K)
    else:
        delta = -disc_q * _N.cdf(-d1)
        theta_annual = (
            -(spot * disc_q * pdf_d1 * vol) / (2 * sqrt_t)
            + rate * strike * disc_r * _N.cdf(-d2)
            - dividend_yield * spot * disc_q * _N.cdf(-d1)
        )
        rho = -strike * years * disc_r * _N.cdf(-d2)
        prob_itm_rn = _N.cdf(-d2)  # risk-neutral P(S_T < K)

    gamma = disc_q * pdf_d1 / (spot * vol * sqrt_t)  # same for call/put
    vega = spot * disc_q * pdf_d1 * sqrt_t  # same for call/put
    intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)

    return {
        "option_type": option_type,
        "price": price,
        "intrinsic_value": intrinsic,
        "time_value": price - intrinsic,
        "delta": delta,
        "gamma": gamma,
        "vega_per_point": vega / 100.0,  # per 1 vol point (the usual convention)
        "theta_per_day": theta_annual / 365.0,  # per calendar day
        "rho_per_1pct": rho / 100.0,  # per 100bp rate move
        "prob_itm_risk_neutral": prob_itm_rn,
        "d1": d1,
        "d2": d2,
        "inputs": {
            "spot": spot,
            "strike": strike,
            "years": years,
            "rate": rate,
            "vol": vol,
            "dividend_yield": dividend_yield,
        },
    }


class BlackScholesRequest(BaseModel):
    """Input for a European Black-Scholes-Merton price + Greeks."""

    model_config = ConfigDict(allow_inf_nan=False)  # reject NaN/inf inputs

    spot: float = Field(..., gt=0, description="Current underlying price S.")
    strike: float = Field(..., gt=0, description="Strike price K.")
    years: float = Field(..., gt=0, le=30, description="Time to expiry in years (e.g. 0.5).")
    rate: float = Field(
        ..., ge=-0.1, le=1, description="Continuously-compounded risk-free rate, decimal p.a."
    )
    vol: float = Field(..., gt=0, le=5, description="Volatility of log returns, decimal p.a.")
    option_type: Literal["call", "put"] = Field("call", description="call or put.")
    dividend_yield: float = Field(
        0.0, ge=0, le=1, description="Continuous dividend yield q (foreign rate for FX)."
    )


async def _handler(req: BlackScholesRequest) -> Result:
    r = black_scholes(
        req.spot,
        req.strike,
        req.years,
        req.rate,
        req.vol,
        option_type=req.option_type,
        dividend_yield=req.dividend_yield,
    )
    return Result(BlackScholesOutput(**r))


SPEC = ToolSpec(
    name="compute_derivatives_black_scholes",
    display_name="compute.derivatives.black_scholes",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=BlackScholesRequest,
    handler=_handler,
    output=BlackScholesOutput,
    tags=("compute", "derivatives", "options", "black-scholes", "greeks"),
)
