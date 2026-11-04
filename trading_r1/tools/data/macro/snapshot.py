# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.macro.snapshot: the latest of a macro category, each series with its changes.

A dashboard read: for one of Tauric's categories (inflation, labour, growth, yields, policy
rates, credit and stress, consumer, housing and business, trade, money supply), every series'
newest observation with its change on the previous period and on the year before, as Tauric
computes them. For a series' history, data.macro.series.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from trading_r1.contract.spec import (
    Output,
    Provenance,
    Result,
    ToolError,
    ToolSpec,
    hint,
    rendered,
)
from trading_r1.contract.units import Code, Day, Fraction, Price, Text
from trading_r1.tools.data.macro.common import CATEGORIES, RATE_UNITS, unit_text
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "The latest US macro readings for one category (Growth & Output, Employment & Labor, "
    "Inflation, Treasury Yields, Fed Policy & Rates, Credit & Stress, Consumer & Income, "
    "Housing & Business, Trade & Global, Money Supply): each series' newest value and period "
    "with its change on the previous period and on the year before (percentage points for "
    "rates, percent for a positive level, otherwise the amount), the publisher's figures, and "
    "the series key. Use to frame the economy or a "
    "theme before drilling into one series with data.macro.series (keys are in `series`)."
)
Category = Literal[CATEGORIES]  # type: ignore[valid-type]


class MacroSnapshotRequest(BaseModel):
    """Input for a macro category snapshot."""

    category: Category = Field(..., description="The category to read.")


# Tauric's change bases: by period for periodic series, by stored observation for daily ones
_BASES = {
    "previous": ("previous_period", "previous_stored"),
    "year_ago": ("year_ago", "year_ago_nearest_stored"),
}


class Reading(BaseModel):
    """One series' newest value and its changes, each in the series' unit (``unit``)."""

    series: Code
    name: Code
    period: Code
    period_end: Annotated[Day, hint(hidden=True)]  # the period, as a date
    value: Price  # a level, read to two decimals
    unit: Code
    previous_from: Annotated[Code, hint(hidden=True)] = None  # the period before
    previous_change: Price = None
    previous_relative: Fraction = None
    year_ago_from: Annotated[Code, hint(hidden=True)] = None  # the same period a year earlier
    year_ago_change: Price = None
    year_ago_relative: Fraction = None


class MacroSnapshotOutput(Output):
    model_config = rendered(
        then="Changes are in each series' unit (percentage points for a % series); a relative "
        "change is over a positive level."
    )

    category: Code
    series: Annotated[list[Reading], hint(rows=20)]  # a category whole (at most ~15)
    notes: list[Text] = []


def _change(changes: list[dict[str, Any]], basis: str, unit: dict[str, Any]) -> dict[str, Any]:
    """Tauric's change on `basis` as {<basis>_from, <basis>_change, <basis>_relative}: the
    absolute change in the series' own unit (Tauric may state it at another multiplier) and,
    for a level that is not a rate, the relative change."""
    c = next((c for c in changes if c.get("basis") in _BASES[basis]), None)
    if c is None or c.get("absolute") is None:
        return {}
    stated = (c.get("absolute_unit") or unit).get("multiplier") or 1.0
    own = unit.get("multiplier") or 1.0
    out = {
        f"{basis}_from": (c.get("from_period") or {}).get("label"),
        f"{basis}_change": c["absolute"] * stated / own,
    }
    if unit.get("kind") not in RATE_UNITS and c.get("change_percent") is not None:
        out[f"{basis}_relative"] = c["change_percent"] / 100
    return out


def _row(key: str, m: dict[str, Any]) -> Reading | None:
    """A member's reading, or None when it has none."""
    s, latest = m.get("series") or {}, m.get("latest") or {}
    if latest.get("value") is None:
        return None
    unit = s.get("unit") or {}
    ch = m.get("changes") or []
    return Reading(
        series=key.removeprefix("series:"),
        name=s.get("name"),
        period=(latest.get("period") or {}).get("label"),
        period_end=(latest.get("period") or {}).get("end"),
        value=latest["value"],
        unit=unit_text(unit),
        **_change(ch, "previous", unit),
        **_change(ch, "year_ago", unit),
    )


async def _handler(req: MacroSnapshotRequest) -> Result:
    async with Tauric() as api:
        body = await api.get("/markets/macro/snapshot", category=req.category)
    rows = [r for m in body.get("data") or [] if (r := _row(m["key"], m))]
    if not rows:
        raise ToolError(f"no {req.category} readings available right now")
    ends = [r.period_end for r in rows if r.period_end]
    return Result(
        MacroSnapshotOutput(category=req.category, series=rows, notes=api.limits),
        Provenance(**provenance(subject=req.category, as_of=max(ends, default=None))),
    )


SPEC = ToolSpec(
    name="data_macro_snapshot",
    display_name="data.macro.snapshot",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MacroSnapshotRequest,
    handler=_handler,
    output=MacroSnapshotOutput,
    tags=("macro", "economics", "dashboard", "data"),
)
