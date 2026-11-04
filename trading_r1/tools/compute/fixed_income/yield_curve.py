# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.fixed_income.yield_curve: zero rates, discount factors, forwards and spreads.

From (maturity, rate) points (e.g. the Treasury tenors, chained as `prev:<id>.curve` from
data.market.rates) builds a usable curve:

- bootstrap par yields to zero (spot) rates and discount factors. A point maturing
  within one coupon period is a zero-coupon money-market instrument, DF = 1/(1 + r*t). Longer
  points are par yields: interpolated (linearly, which gives kinked forwards) onto the coupon
  grid k/frequency, each discount factor solving a par bond priced at 1,
  DFᵢ = (1 − cᵢ*Σ_{j<i} DFⱼ) / (1 + cᵢ). A non-positive discount factor means the input curve
  admits no consistent zero curve, and the tool says at which maturity. With
  ``rate_type="zero"`` the inputs are used directly;
- interpolate at query maturities (linear, flat beyond the ends) both the observed yield
  (the benchmark for a spread quoted over the curve, a G-spread) and the zero rate, which
  discounts a single cash flow;
- forward rates between consecutive input maturities at least one coupon period apart, from
  discount-factor ratios, with the minimum reported (a negative forward means the curve falls
  faster than carry allows);
- spreads on the observed input rates (the market quoting convention): long minus short,
  and 10y−2y, 10y−3m, 30y−5y when both tenors are observed points (an interpolated tenor is not
  the quoted spread). Numbers only; no shape labels.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Number

_DESCRIPTION = (
    "Yield-curve analytics from (maturity in years, rate as a decimal) points: chain "
    "`prev:<id>.curve` from data.market.rates. rate_type par (observed yields, bootstrapped to "
    "zero rates; default) or zero. Returns zero rates and discount factors per maturity, "
    "forward rates between maturities, the interpolated observed yield (the spread benchmark) "
    "and zero rate at any query_maturities, and the curve's "
    "spreads (long−short, and 10y−2y, 10y−3m, 30y−5y when both tenors are given). "
    "frequency default 2."
)
_NAMED_SPREADS = (
    ("10y_minus_2y", 2.0, 10.0),
    ("10y_minus_3m", 0.25, 10.0),
    ("30y_minus_5y", 5.0, 30.0),
)


class CurvePoint(BaseModel):
    """One observed point on the curve."""

    model_config = ConfigDict(allow_inf_nan=False)

    maturity: float = Field(..., gt=0, le=100, description="Maturity in years.")
    rate: float = Field(..., gt=-0.1, lt=1, description="Rate, decimal p.a. (0.0445).")


class YieldCurveRequest(BaseModel):
    """Input for yield-curve analytics."""

    model_config = ConfigDict(allow_inf_nan=False)

    points: list[CurvePoint] = Field(..., min_length=2, max_length=60)
    rate_type: Literal["par", "zero"] = Field("par", description="par (observed) or zero rates.")
    frequency: int = Field(2, ge=1, le=12, description="Coupons per year (2 = US semiannual).")
    query_maturities: list[Annotated[float, Field(gt=0, le=100)]] | None = Field(
        None, max_length=60, description="Maturities (years) to interpolate at (optional)."
    )

    @model_validator(mode="after")
    def _unique(self) -> YieldCurveRequest:
        mats = [p.maturity for p in self.points]
        if len(set(mats)) != len(mats):
            raise ValueError("duplicate maturities in points")
        self.points.sort(key=lambda p: p.maturity)
        return self


class CurveRow(BaseModel):
    maturity: Number  # years
    input_rate: Fraction
    zero_rate: Fraction
    discount_factor: Number


class Forward(BaseModel):
    start: Number
    end: Number
    forward_rate: Fraction


class Queried(BaseModel):
    """A query maturity: the observed yield (the spread benchmark) and the zero rate."""

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True)

    maturity: Number
    yield_: Fraction = Field(alias="yield")
    zero_rate: Fraction
    discount_factor: Number


class YieldCurveOutput(Output):
    rate_type: Code
    frequency: Count
    curve: list[CurveRow]
    forward_rates: list[Forward]
    min_forward_rate: Fraction
    spreads: dict[str, Fraction]  # on the observed rates: long minus short, named tenors
    queried: list[Queried] = []


def _df(zero: float, t: float, freq: int) -> float:
    return (1 + zero / freq) ** (-freq * t)


def _zero(df: float, t: float, freq: int) -> float:
    return freq * (df ** (-1 / (freq * t)) - 1)


def bootstrap(mats: list[float], par: list[float], freq: int) -> list[float]:
    """Zero rates at ``mats`` bootstrapped from par yields (short points as zero-coupon)."""
    period = 1 / freq
    grid = [k / freq for k in range(1, math.ceil(mats[-1] * freq - 1e-9) + 1)]
    coupons = np.interp(grid, mats, par) / freq
    dfs: list[float] = []
    for t, c in zip(grid, coupons, strict=True):
        df = (1 - c * sum(dfs)) / (1 + c)
        if df <= 0:
            raise ToolError(
                f"the par curve admits no positive discount factor at {t:g}y: the curve steepens "
                "faster than a consistent zero curve allows; check the inputs"
            )
        dfs.append(df)
    grid_zeros = [_zero(df, t, freq) for df, t in zip(dfs, grid, strict=True)]
    return [
        _zero(1 / (1 + r * t), t, freq) if t <= period else float(np.interp(t, grid, grid_zeros))
        for t, r in zip(mats, par, strict=True)
    ]


def compute_yield_curve(req: YieldCurveRequest) -> dict[str, Any]:
    freq = req.frequency
    mats = [p.maturity for p in req.points]
    rates = [p.rate for p in req.points]
    zeros = bootstrap(mats, rates, freq) if req.rate_type == "par" else rates
    dfs = [_df(z, t, freq) for z, t in zip(zeros, mats, strict=True)]
    forwards = [
        {
            "start": mats[i - 1],
            "end": mats[i],
            "forward_rate": freq
            * ((dfs[i - 1] / dfs[i]) ** (1 / (freq * (mats[i] - mats[i - 1]))) - 1),
        }
        for i in range(1, len(mats))
        if mats[i] - mats[i - 1] >= 1 / freq  # shorter gaps give no meaningful forward
    ]
    observed = dict(zip(mats, rates, strict=True))
    spreads = {"long_minus_short": rates[-1] - rates[0]}
    for name, short, long in _NAMED_SPREADS:
        if short in observed and long in observed:
            spreads[name] = observed[long] - observed[short]
    queried = []
    for q in req.query_maturities or []:
        z = float(np.interp(q, mats, zeros))
        queried.append(
            {
                "maturity": q,
                "yield": float(np.interp(q, mats, rates)),
                "zero_rate": z,
                "discount_factor": _df(z, q, freq),
            }
        )
    return {
        "rate_type": req.rate_type,
        "frequency": freq,
        "curve": [
            {"maturity": t, "input_rate": r, "zero_rate": z, "discount_factor": df}
            for t, r, z, df in zip(mats, rates, zeros, dfs, strict=True)
        ],
        "forward_rates": forwards,
        "min_forward_rate": min((f["forward_rate"] for f in forwards), default=None),
        "spreads": spreads,
        "queried": queried,
    }


async def _handler(req: YieldCurveRequest) -> Result:
    return Result(YieldCurveOutput(**compute_yield_curve(req)))


SPEC = ToolSpec(
    name="compute_fixed_income_yield_curve",
    display_name="compute.fixed_income.yield_curve",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=YieldCurveRequest,
    handler=_handler,
    output=YieldCurveOutput,
    tags=("compute", "fixed_income", "yield-curve", "zero-rates", "forwards"),
)
