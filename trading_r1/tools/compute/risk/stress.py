# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.stress: scenario P&L from position sensitivities (delta-gamma).

The sensitivities-based scenario method: each position is a set of signed first-order
sensitivities ($ P&L per unit move of a risk factor) plus optional convexities, and each
scenario is a set of factor shocks, so
``P&L(position, scenario) = Σ_factor [sensitivity*shock + ½*convexity*shock²]``.
A long equity position's sensitivity to its own return is its market value; a bond's to its
yield is −DV01 (per bp) with convexity the second-order term; an option book's to spot is its
dollar delta with gamma the convexity.

The caller supplies every number: no canned scenarios, factor magnitudes or pass/fail limits.
The tool reports each scenario's P&L decomposed by position and factor, ranks scenarios by P&L,
and flags shocks that match no sensitivity. Units are the caller's, consistent per factor.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Fraction, Money

_DESCRIPTION = (
    "What-if P&L of a portfolio under hypothetical shocks ('stocks fall 30% and rates rise "
    "100bp', a sector crash, a currency move): the forward-looking scenario test (for how it "
    "fared historically, use prices and drawdown instead). Scenario / stress P&L from "
    "position sensitivities. Give each position its signed "
    "sensitivities ($ P&L per +1 unit of each factor) with optional convexities, and each "
    "scenario its signed factor shocks; returns P&L = Σ sensitivity*shock + "
    "½*convexity*shock² per scenario, decomposed by position and factor, ranked worst to "
    "best, optionally as % of base_value. Signs follow the P&L: a $1M long stock is "
    "{'equity': 1000000} for a return shock like -0.2; a long bond with an $800 DV01 loses "
    "when yields rise, so it is {'rate_bp': -800} (convexity positive) for a shock like 100. "
    "You choose the scenarios; the tool supplies none."
)


class StressPosition(BaseModel):
    """One position: its signed sensitivities (and optional convexities) per risk factor."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(..., description="Position name (e.g. symbol or leg).")
    sensitivities: dict[str, float] = Field(
        ..., min_length=1, description="Map of factor to signed $ P&L per unit shock."
    )
    convexities: dict[str, float] | None = Field(
        None, description="Map of factor to signed $ P&L per unit shock² (gamma / convexity)."
    )


class StressScenario(BaseModel):
    """One scenario: the signed shock applied to each risk factor."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(..., description="Scenario name (e.g. 'rates +100bp, equity −20%').")
    shocks: dict[str, float] = Field(
        ..., min_length=1, description="Map of factor to signed shock, in the sensitivity's units."
    )


class StressRequest(BaseModel):
    """Input for scenario P&L over a book of positions."""

    model_config = ConfigDict(allow_inf_nan=False)

    positions: list[StressPosition] = Field(..., min_length=1, max_length=200)
    scenarios: list[StressScenario] = Field(..., min_length=1, max_length=100)
    base_value: float | None = Field(
        None, gt=0, description="Portfolio value, to express P&L as a percentage (optional)."
    )
    include_second_order: bool = Field(
        True, description="Apply ½*convexity*shock² where convexities are given."
    )

    @model_validator(mode="after")
    def _unique_names(self) -> StressRequest:
        for kind, items in (("position", self.positions), ("scenario", self.scenarios)):
            names = [i.name for i in items]
            if len(set(names)) != len(names):
                raise ValueError(f"{kind} names must be unique")
        return self


class PositionPnl(BaseModel):
    name: Code
    pnl: Money
    pct_of_base: Fraction = None


class FactorPnl(BaseModel):
    factor: Code
    pnl: Money


class ScenarioPnl(BaseModel):
    name: Code
    total_pnl: Money
    pct_of_base: Fraction = None
    positions: list[PositionPnl]
    factors: list[FactorPnl]  # largest |P&L| first
    unmatched_shocks: list[Code]  # shocks no position has a sensitivity to


class StressOutput(Output):
    worst: Code
    best: Code
    base_value: Money = None
    include_second_order: bool
    scenarios: list[ScenarioPnl]


def _position_pnl(
    pos: StressPosition, shocks: dict[str, float], second_order: bool
) -> dict[str, float]:
    """Per-factor P&L of one position under one scenario (factors it has no exposure to omit)."""
    conv = pos.convexities or {}
    out: dict[str, float] = {}
    for factor, shock in shocks.items():
        pnl = pos.sensitivities.get(factor, 0.0) * shock
        if second_order:
            pnl += 0.5 * conv.get(factor, 0.0) * shock * shock
        if pnl:
            out[factor] = pnl
    return out


def compute_stress(req: StressRequest) -> dict[str, Any]:
    base = req.base_value
    known = {f for p in req.positions for f in (*p.sensitivities, *(p.convexities or {}))}
    scenarios = []
    for sc in req.scenarios:
        by_factor: dict[str, float] = {}
        positions = []
        for pos in req.positions:
            contrib = _position_pnl(pos, sc.shocks, req.include_second_order)
            for f, v in contrib.items():
                by_factor[f] = by_factor.get(f, 0.0) + v
            pnl = sum(contrib.values())
            positions.append(
                {"name": pos.name, "pnl": pnl, "pct_of_base": pnl / base if base else None}
            )
        total = sum(p["pnl"] for p in positions)
        scenarios.append(
            {
                "name": sc.name,
                "total_pnl": total,
                "pct_of_base": total / base if base else None,
                "positions": positions,
                "factors": [
                    {"factor": f, "pnl": v}
                    for f, v in sorted(by_factor.items(), key=lambda kv: -abs(kv[1]))
                ],
                "unmatched_shocks": sorted(set(sc.shocks) - known),
            }
        )
    if not all(math.isfinite(s["total_pnl"]) for s in scenarios):
        raise ToolError("scenario P&L overflowed: check the magnitudes of sensitivities and shocks")
    ranked = sorted(scenarios, key=lambda s: s["total_pnl"])
    return {
        "scenarios": scenarios,
        "worst": ranked[0]["name"],
        "best": ranked[-1]["name"],
        "base_value": base,
        "include_second_order": req.include_second_order,
    }


async def _handler(req: StressRequest) -> Result:
    return Result(StressOutput(**compute_stress(req)))


SPEC = ToolSpec(
    name="compute_risk_stress",
    display_name="compute.risk.stress",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=StressRequest,
    handler=_handler,
    output=StressOutput,
    tags=("compute", "risk", "stress", "scenario"),
)
