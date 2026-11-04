# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.vol_term_structure: the VIX term structure.

Cboe's implied-volatility indices on the S&P 500 at 9 days, 30 days (the VIX),
3 months, 6 months and 1 year, with Tauric's read of the curve: contango (longer horizons
higher, the calm-market norm) or backwardation (near-term stress priced above the long run), and
the slope. Levels are annualized volatility in percentage points.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Percent, Points, Text
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "The VIX term structure: Cboe's S&P 500 implied-volatility indices at 9 days, 30 days "
    "(VIX), 3 months, 6 months and 1 year (each level, annualized vol in points, its day "
    "change and its change over five sessions), with the curve's shape (contango: longer "
    "horizons higher; backwardation: near-term stress priced above the long run), both judged on "
    "VIX3M against VIX, and the slope (VIX3M − VIX, in vol points). "
    "For one index's history use data.market.prices (^VIX)."
)
# Tauric field to (label, horizon in days)
_TENORS = {
    "vix_9d": ("VIX9D", 9),
    "spot": ("VIX", 30),
    "vix_3m": ("VIX3M", 91),
    "vix_6m": ("VIX6M", 182),
    "vix_1y": ("VIX1Y", 365),
}


class VolTermStructureRequest(BaseModel):
    """Input for the VIX term structure (none)."""


class Point(BaseModel):
    index: Code
    days: Count
    level: Percent  # annualized volatility, in percent
    change: Points
    change_5d: Points = None
    change_5d_from: Code = None


class VolTermStructureOutput(Output):
    """The curve, shortest horizon first, with its shape and slope (VIX3M − VIX)."""

    session_date: Day = None
    structure: Code = None
    slope: Points = None
    curve: list[Point]
    notes: list[Text] = []


def _point(q: dict[str, Any]) -> dict[str, Any]:
    five = q.get("change_5d") or {}
    return {
        "level": q.get("price"),
        "change": q.get("change"),
        "change_5d": five.get("absolute"),
        "change_5d_from": (five.get("from_period") or {}).get("label"),
    }


async def _handler(_req: VolTermStructureRequest) -> Result:
    async with Tauric() as api:
        body = await api.get("/series/vix-term-structure")
    curve = [
        Point(index=label, days=days, **_point(body[field]))
        for field, (label, days) in _TENORS.items()
        if (body.get(field) or {}).get("price") is not None
    ]
    if not curve:
        raise ToolError("the VIX term structure is unavailable right now")
    out = VolTermStructureOutput(
        session_date=body.get("session_date"),
        structure=body.get("structure"),
        slope=body.get("slope"),
        curve=curve,
        notes=api.limits,
    )
    return Result(
        out,
        Provenance(
            **provenance(subject="VIX curve", as_of=body.get("session_date"), source="Cboe")
        ),
    )


SPEC = ToolSpec(
    name="data_market_vol_term_structure",
    display_name="data.market.vol_term_structure",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=VolTermStructureRequest,
    handler=_handler,
    output=VolTermStructureOutput,
    tags=("market", "volatility", "vix", "term-structure", "data"),
)
