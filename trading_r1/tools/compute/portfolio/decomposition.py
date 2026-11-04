# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.portfolio.decomposition: a fixed-weight portfolio's return and risk, by holding.

Combines named price series with weights into the portfolio's simple-return series and
decomposes it: each holding's contribution to annualized return (wᵢ*μᵢ, summing to the
portfolio's) and to risk (wᵢ*(Σw)ᵢ / wᵀΣw, summing to 100%, the Euler allocation), plus the
diversification ratio (weighted-average holding volatility / portfolio volatility; defined for
long-only books, null with shorts). Weights are scaled to unit gross exposure (Σ|wᵢ| = 1), which
keeps a long/short book's proportions and allows dollar-neutral books. The
portfolio's return series is emitted for chaining into compute.portfolio.performance,
compute.risk.var or compute.risk.drawdown, which report Sharpe, VaR and drawdowns.
"""

from __future__ import annotations

import math
from typing import Annotated, Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Fraction, Number
from trading_r1.tools.compute.portfolio.moments import (
    NamedPrices,
    annualized_moments,
    return_matrix,
    risk_contributions,
)
from trading_r1.tools.compute.series import ALIGNMENT_NOTE, PeriodsPerYear

_DESCRIPTION = (
    "Return and risk decomposition of a fixed-weight portfolio. Pass prices as a map of "
    "name to price series (`closes` per holding) and "
    "weights as a map of name to weight (scaled to unit gross exposure; negative = short). Returns "
    "the portfolio's annualized return and volatility, each holding's contribution to return "
    "and to risk, the diversification ratio, and the portfolio return series (`returns`) to "
    "chain into performance, var or drawdown." + ALIGNMENT_NOTE
)


class DecompositionRequest(BaseModel):
    """Input for decomposing a fixed-weight portfolio."""

    model_config = ConfigDict(allow_inf_nan=False)

    prices: NamedPrices = Field(..., description="Map of holding to price series.")
    weights: dict[str, float] = Field(..., description="Map of holding to weight.")
    periods_per_year: PeriodsPerYear = Field(252, description="Bars per year (252 daily).")

    @model_validator(mode="after")
    def _match(self) -> DecompositionRequest:
        if set(self.weights) != set(self.prices):
            raise ValueError("weights must name exactly the holdings in prices")
        if not any(self.weights.values()):
            raise ValueError("all weights are zero")
        return self


class Holding(BaseModel):
    name: Code
    weight: Fraction
    annual_return: Fraction
    annual_volatility: Fraction
    return_contribution: Fraction
    risk_contribution: Fraction = None  # undefined for a riskless book


class DecompositionOutput(Output):
    annual_return: Fraction
    annual_volatility: Fraction
    weighted_avg_volatility: Fraction
    diversification_ratio: Number = None  # long-only books
    holdings: list[Holding]
    returns: Annotated[list[Fraction], hint(hidden=True)]
    periods_per_year: Number
    n_periods: Count


def compute_decomposition(req: DecompositionRequest) -> dict[str, Any]:
    names, r = return_matrix(req.prices)
    gross = sum(abs(v) for v in req.weights.values())
    w = np.array([req.weights[k] / gross for k in names])
    mu, cov = annualized_moments(r, req.periods_per_year)
    port = w @ r
    port_vol = float(math.sqrt(max(float(w @ cov @ w), 0.0)))
    vols = np.sqrt(np.diag(cov))
    rc = risk_contributions(w, cov)
    weighted_vol = float(w @ vols)
    return {
        "annual_return": float(w @ mu),
        "annual_volatility": port_vol,
        "weighted_avg_volatility": weighted_vol,
        "diversification_ratio": (weighted_vol / port_vol if port_vol and (w >= 0).all() else None),
        "holdings": [
            {
                "name": k,
                "weight": float(w[i]),
                "annual_return": float(mu[i]),
                "annual_volatility": float(vols[i]),
                "return_contribution": float(w[i] * mu[i]),
                "risk_contribution": None if rc is None else float(rc[i]),
            }
            for i, k in enumerate(names)
        ],
        "returns": port.tolist(),
        "periods_per_year": req.periods_per_year,
        "n_periods": r.shape[1],
    }


async def _handler(req: DecompositionRequest) -> Result:
    return Result(DecompositionOutput(**compute_decomposition(req)))


SPEC = ToolSpec(
    name="compute_portfolio_decomposition",
    display_name="compute.portfolio.decomposition",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DecompositionRequest,
    handler=_handler,
    output=DecompositionOutput,
    tags=("compute", "portfolio", "risk-contribution", "diversification"),
)
