# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What the macro tools share: series keys, units, and the changes a reader asks for first.

Tauric's reference series carry their unit (kind, multiplier, measure, per) and their period
(a month, quarter, week, day, marketing year). A value is shown in its unit; a series measured
in percent or percentage points changes in percentage points, any other by an absolute and a
relative amount. The year-ago observation is matched by date, so a missing month makes the
year-on-year change unknown rather than wrong.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# Tauric's categories for economic and rate series (its `category` filter's vocabulary).
CATEGORIES = (
    "Growth & Output",
    "Employment & Labor",
    "Inflation",
    "Treasury Yields",
    "Fed Policy & Rates",
    "Credit & Stress",
    "Consumer & Income",
    "Housing & Business",
    "Trade & Global",
    "Money Supply",
)
RATE_UNITS = frozenset({"percent", "percentage_points"})
# observations a year at each frequency, for chaining into the compute tools (a daily series
# that publishes on every calendar day has 365; `periods_per_year` decides)
PERIODS_PER_YEAR = {"daily": 252, "weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1}
_CALENDAR_DAYS = 365
_WEEKEND = (5, 6)
YEAR_SLACK_DAYS = 7  # month and week ends drift a few days from one year to the next
YEAR_DAYS = 365
_CENT = 0.01


def series_key(name: str) -> str:
    """A series key as the routes take it: `us.cpi.all_items.sa` (a `series:` prefix dropped)."""
    return name.strip().removeprefix("series:").lower()


def unit_text(unit: dict[str, Any]) -> str:
    """A short label for a unit: `%`, `pp`, `USD bn`, `thousand barrels / day`, `index`."""
    kind, mult = unit.get("kind"), unit.get("multiplier") or 1.0
    scale = {1e3: "thousand ", 1e6: "million ", 1e9: "billion "}.get(mult, "")
    if kind == "percent":
        return "%"
    if kind == "percentage_points":
        return "pp"
    if kind == "currency" and mult == _CENT:
        base = f"{unit.get('currency') or ''} cents".strip()
    elif kind == "currency":
        base = f"{scale}{unit.get('currency') or ''}".strip()
    elif kind == "quantity":
        base = f"{scale}{(unit.get('measure') or '').replace('_', ' ')}s".strip()
    elif kind == "count":
        base = f"{scale}units".strip() if scale else "count"
    else:
        base = kind or ""
    return f"{base} / {unit['per'].replace('_', ' ')}" if unit.get("per") else base


def value_units(unit: dict[str, Any]) -> tuple[str, str]:
    """The units a series' values and their changes read in: a rate in percent (its changes
    in percentage points), a series in percentage points (as are its changes), else a plain
    level counted in the series' `unit_text` (read to two decimals, as published)."""
    kind = unit.get("kind")
    if kind == "percent":
        return "percent", "points"
    if kind == "percentage_points":
        return "points", "points"
    return "price", "price"


def _end(obs: dict[str, Any]) -> dt.date:
    return dt.date.fromisoformat(obs["period"]["end"])


def periods_per_year(frequency: str | None, obs: list[dict[str, Any]]) -> int | None:
    """Observations a year: the frequency's, or 365 for a daily series that publishes on
    weekends (policy and money-market rates)."""
    if frequency == "daily" and any(_end(o).weekday() in _WEEKEND for o in obs):
        return _CALENDAR_DAYS
    return PERIODS_PER_YEAR.get(frequency or "")


def year_ago(last: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The observation closest to a year before `last`, within a week either side."""
    near = [
        o
        for o in candidates
        if o.get("value") is not None
        and abs((_end(last) - _end(o)).days - YEAR_DAYS) <= YEAR_SLACK_DAYS
    ]
    return min(near, key=lambda o: abs((_end(last) - _end(o)).days - YEAR_DAYS), default=None)


def change(last: dict[str, Any], ref: dict[str, Any] | None, unit: dict[str, Any]) -> dict | None:
    """`last` against `ref`: the absolute change in the series' unit and, for a level over a
    positive base, the relative change (a ratio over a negative or zero base has no meaning)."""
    if ref is None:
        return None
    out = {"from_period": ref["period"]["label"], "absolute": last["value"] - ref["value"]}
    if unit.get("kind") not in RATE_UNITS and ref["value"] > 0:
        out["relative"] = last["value"] / ref["value"] - 1
    return out
