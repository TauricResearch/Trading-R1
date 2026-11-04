# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.derivatives.binomial: American and European options on a CRR binomial tree.

`compute.derivatives.black_scholes` is European-only. Most listed equity options are American,
and early exercise has value for a put, or for a call on a dividend payer. This prices either
style on a Cox-Ross-Rubinstein tree and reports the early-exercise premium (American − European
on the same tree) beside the BSM value for reference.

CRR: Δt = T/N, u = e^{σ√Δt}, d = 1/u, risk-neutral p = (e^{(r−q)Δt} − d)/(u − d); backward
induction discounts each step and, for American exercise, takes max(continuation, intrinsic) at
every node. The tree is valid only when d < e^{(r−q)Δt} < u, i.e. 0 < p < 1; otherwise it asks
for more steps. Delta, gamma and theta come from the tree's own early nodes (Hull, *Options,
Futures, and Other Derivatives*, the tree-Greeks scheme: no re-gridding noise); vega and rho
are central differences. Greeks use the black_scholes units (vega per vol point, theta per
calendar day, rho per 1% rate).
"""

from __future__ import annotations

import asyncio
import math
import sys
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Number, Price
from trading_r1.tools.compute.derivatives.black_scholes import GREEKS, bsm_price

_DESCRIPTION = (
    "Price an American or European option on a CRR binomial tree (use this for American "
    "exercise, which most listed equity options have; black_scholes is European-only). Give spot, "
    "strike, years to expiry, continuously-compounded rate and volatility (decimals p.a.), "
    "option_type call|put, dividend_yield (default 0), exercise american|european, steps "
    "(default 500, max 2000). Returns the price, the early-exercise premium over the European "
    "value, "
    "the BSM reference, and delta, gamma, vega (per vol point), theta (per day), rho (per 1%)."
)
# The largest node exponent a float can hold: the tree's top node is S*e^{σ√(T*N)}.
_MAX_EXPONENT = math.log(sys.float_info.max)
# Central-difference bumps for vega and rho: one vol point (capped at half the vol, so the
# lower bump stays positive) and one basis point.
_VOL_BUMP = 0.01
_RATE_BUMP = 0.0001


class BinomialRequest(BaseModel):
    """Input for a CRR binomial-tree option price."""

    model_config = ConfigDict(allow_inf_nan=False)

    spot: float = Field(..., gt=0, description="Current underlying price S.")
    strike: float = Field(..., gt=0, description="Strike price K.")
    years: float = Field(..., gt=0, le=30, description="Time to expiry in years.")
    rate: float = Field(..., ge=-0.1, le=1, description="Continuously-compounded rate, p.a.")
    vol: float = Field(..., gt=0, le=5, description="Volatility, decimal p.a.")
    option_type: Literal["call", "put"] = Field("call", description="call or put.")
    dividend_yield: float = Field(0.0, ge=0, le=1, description="Continuous dividend yield q.")
    exercise: Literal["american", "european"] = Field("american", description="Exercise style.")
    steps: int = Field(500, ge=10, le=2000, description="Tree steps (more = more accurate).")


class BinomialOutput(Output):
    option_type: Code
    exercise: Code
    price: Price
    european_price: Price  # on the same tree
    early_exercise_premium: Price
    bsm_price: Price
    delta: Number
    gamma: Number
    vega_per_point: Number
    theta_per_day: Number
    rho_per_1pct: Number
    steps: Count


def crr_tree(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    vol: float,
    *,
    steps: int,
    call: bool,
    american: bool,
    dividend_yield: float = 0.0,
) -> tuple[float, list[np.ndarray], float, float, float]:
    """(root value, [step-1 values, step-2 values], dt, u, d) by backward induction."""
    dt = years / steps
    step = vol * math.sqrt(dt)
    u, d = math.exp(step), math.exp(-step)
    disc = math.exp(-rate * dt)
    p = (math.exp((rate - dividend_yield) * dt) - d) / (u - d)
    if not 0 < p < 1:
        needed = math.ceil(years * ((rate - dividend_yield) / vol) ** 2) + 1
        raise ToolError(
            f"the tree's risk-neutral probability is {p:.3f}, outside (0, 1): volatility is too "
            f"low for the rate over each step; use at least {needed} steps"
        )
    if step * steps >= _MAX_EXPONENT:
        fits = int((_MAX_EXPONENT / vol) ** 2 / years)
        raise ToolError(
            f"a {steps}-step tree at this volatility and expiry exceeds floating-point range; "
            f"use at most {fits} steps"
        )
    sign = 1 if call else -1

    def spots(level: int) -> np.ndarray:  # S*u^j*d^(level−j), in log form to avoid overflow
        return spot * np.exp(step * (2 * np.arange(level + 1) - level))

    values = np.maximum(sign * (spots(steps) - strike), 0.0)
    early: list[np.ndarray] = []
    for i in range(steps - 1, -1, -1):
        values = disc * (p * values[1 : i + 2] + (1 - p) * values[: i + 1])
        if american:
            values = np.maximum(values, sign * (spots(i) - strike))
        if i in (1, 2):
            early.insert(0, values.copy())
    return float(values[0]), early, dt, u, d


def compute_binomial(req: BinomialRequest) -> dict[str, Any]:
    call, american = req.option_type == "call", req.exercise == "american"

    def price(vol: float = req.vol, rate: float = req.rate, style: bool = american) -> float:
        return crr_tree(
            req.spot,
            req.strike,
            req.years,
            rate,
            vol,
            steps=req.steps,
            call=call,
            american=style,
            dividend_yield=req.dividend_yield,
        )[0]

    value, (s1, s2), dt, u, d = crr_tree(
        req.spot,
        req.strike,
        req.years,
        req.rate,
        req.vol,
        steps=req.steps,
        call=call,
        american=american,
        dividend_yield=req.dividend_yield,
    )
    s = req.spot
    delta = (s1[1] - s1[0]) / (s * u - s * d)
    gamma = ((s2[2] - s2[1]) / (s * u * u - s) - (s2[1] - s2[0]) / (s - s * d * d)) / (
        0.5 * (s * u * u - s * d * d)
    )
    theta_year = (s2[1] - value) / (2 * dt)  # same spot two steps later
    h = min(_VOL_BUMP, req.vol / 2)
    vega = (price(vol=req.vol + h) - price(vol=req.vol - h)) / (2 * h)
    rho = (price(rate=req.rate + _RATE_BUMP) - price(rate=req.rate - _RATE_BUMP)) / (2 * _RATE_BUMP)
    european = price(style=False) if american else value
    return {
        "option_type": req.option_type,
        "exercise": req.exercise,
        "price": value,
        "european_price": european,
        "early_exercise_premium": value - european,
        "bsm_price": bsm_price(
            req.spot,
            req.strike,
            req.years,
            req.rate,
            req.vol,
            option_type=req.option_type,
            dividend_yield=req.dividend_yield,
        ),
        **dict(
            zip(
                GREEKS,
                # + 0.0 turns −0.0 into 0.0
                (float(delta), float(gamma), vega / 100 + 0.0, float(theta_year) / 365, rho / 100),
                strict=True,
            )
        ),
        "steps": req.steps,
    }


async def _handler(req: BinomialRequest) -> Result:
    res = await asyncio.to_thread(compute_binomial, req)  # CPU-bound: keep the loop free
    return Result(BinomialOutput(**res), Provenance(method=f"CRR binomial, {req.steps} steps"))


SPEC = ToolSpec(
    name="compute_derivatives_binomial",
    display_name="compute.derivatives.binomial",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=BinomialRequest,
    handler=_handler,
    output=BinomialOutput,
    tags=("compute", "derivatives", "options", "american", "binomial"),
)
