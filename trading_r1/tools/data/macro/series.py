# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.macro.series: find an economic or market reference series, or read its history.

Tauric's registry holds about two hundred US reference series by concept key: output (GDP and
its nowcasts), labour, inflation and breakevens, Treasury yields and spreads, policy and money
market rates, credit spreads and stress indices, consumption, housing and business surveys,
trade, money supply, and physical commodity balances (crude stocks and production, crop supply
and use). One tool both finds a series (by name or category) and reads it: the observations
over a window, oldest first, with the newest against the previous period and the year before,
and on request the projections published for periods not yet observed, kept apart from them.
Daily prices of indices live in the price tools.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import (
    Output,
    Provenance,
    Result,
    ToolError,
    ToolSpec,
    hint,
    rendered,
)
from trading_r1.contract.units import Code, Day, Fraction, Number, Text
from trading_r1.tools.data.macro.common import (
    CATEGORIES,
    YEAR_DAYS,
    YEAR_SLACK_DAYS,
    change,
    periods_per_year,
    series_key,
    unit_text,
    value_units,
    year_ago,
)
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "US economic and market reference series from Tauric's registry (about 200: GDP and "
    "nowcasts, payrolls, unemployment and claims, CPI/PCE/PPI and breakevens, Treasury yields "
    "and spreads, fed funds, SOFR, mortgage rates, corporate credit spreads, financial stress "
    "indices, consumption, sentiment, housing, durable goods, trade, M2 and the Fed balance "
    "sheet, crude and crop balances). Find: give `query` (e.g. 'unemployment', 'core pce') or "
    "`category` to list matching series keys. Read: give `series` (a key such as "
    "us.cpi.core.sa) for its observations, oldest first: `limit` newest, or from `start` "
    "(up to about 2,200 periods: decades of monthly data, eight years of daily), up to `end` "
    "when given, with the newest value against the previous period and the year before; chain "
    "`values`, `dates`, "
    "`periods_per_year`. `projections` adds the forecasts published for periods not yet observed "
    "(USDA's crop balance for the coming marketing year), listed apart from the observations. "
    "Index prices (S&P 500, VIX) are data.market.prices."
)
Category = Literal[CATEGORIES]  # type: ignore[valid-type]
_PAGE = 110  # Tauric's most observations per response
_LIST_MAX = 56  # Tauric's most series per list response
_MAX_PAGES = 20  # a backstop: 2,200 periods (about eight years of a daily series)
_MARKETING_YEAR = "marketing_year"  # Tauric's period kind for crop balances


class MacroSeriesRequest(BaseModel):
    """Find a series, or read one."""

    series: str | None = Field(
        None, max_length=80, description="Series key to read, e.g. us.unemployment_rate.u3.sa."
    )
    query: str | None = Field(None, max_length=80, description="Words to find series by name.")
    category: Category | None = Field(
        None,
        description="A category to list its series (economic and rate series; commodity "
        "balances and credit spreads by rating are found with `query`).",
    )
    limit: int = Field(24, ge=2, le=600, description="Read: the newest observations to return.")
    start: dt.date | None = Field(
        None, description="Read: observations from this date (overrides limit)."
    )
    end: dt.date | None = Field(
        None, description="Read: observations of periods ending by this date (default: the latest)."
    )
    projections: bool = Field(
        False, description="Read: also the projections published for periods not yet observed."
    )

    @model_validator(mode="after")
    def _one_mode(self) -> MacroSeriesRequest:
        if not (self.series or self.query or self.category):
            raise ValueError("give `series` to read one, or `query`/`category` to find series")
        return self


class Match(BaseModel):
    series: Code
    name: Code
    category: Code
    frequency: Code
    unit: Code
    kind: Code


class Latest(BaseModel):
    period: Code
    value: Number


class Projection(BaseModel):
    """A forecast for a period not yet observed, as recorded when Tauric first held it."""

    period: Code
    period_end: Day
    projection: Number
    known_from: Day


class Change(BaseModel):
    from_period: Code
    absolute: Number  # in the series' unit (percentage points for a rate)
    relative: Fraction = None  # a level over a positive base


class Changes(BaseModel):
    previous: Change | None = None
    year_ago: Change | None = None


_ROWS_SHOWN = 60  # the default read (24) whole; a long one as head and tail


class MacroSeriesOutput(Output):
    """A search (``matches``) or a read: the series' description, its newest value against
    the previous period and the year before, and its observations oldest first (``values``
    aligned to ``dates``, the period ends, and ``periods``, their labels). ``projections`` are
    forecasts, not observations, and stay out of those arrays. ``unit`` names what a value counts;
    ``units`` gives values the unit the series is measured in."""

    model_config = rendered(index="dates", rows=_ROWS_SHOWN)

    query: Code = None
    category: Code = None
    matches: list[Match] = []
    series: Code = None
    name: Code = None
    unit: Code = None
    frequency: Code = None
    seasonal_adjustment: Code = None
    latest: Latest | None = None
    changes: Changes | None = None
    truncated: bool | None = None  # the read's page limit cut the window short
    notes: list[Text] = []
    missing: list[Code] = []  # periods not published inside the window
    periods_per_year: Number = None
    dates: list[Day] = []
    periods: list[Code] = []
    values: list[Number] = []
    projections: list[Projection] = []
    notes: list[Text] = []
    units: Annotated[dict[str, Code], hint(hidden=True)] = {}


async def _find(api: Tauric, req: MacroSeriesRequest) -> Result:
    body = await api.get("/series", q=req.query, category=req.category, limit=_LIST_MAX)
    matches = [
        {
            "series": d["key"].removeprefix("series:"),
            "name": d.get("name"),
            "category": d.get("category"),
            "frequency": d.get("frequency"),
            "unit": unit_text(d.get("unit") or {}),
            "kind": d.get("instrument_type"),
        }
        for d in body.get("data") or []
    ]
    if not matches:
        raise ToolError(
            f"no series matched {req.query or req.category!r}: try a shorter name or a category"
        )
    return Result(
        MacroSeriesOutput(query=req.query, category=req.category, matches=matches),
        Provenance(**provenance(subject=req.query or req.category)),
    )


async def _observations(
    api: Tauric, key: str, limit: int, start: dt.date | None, last: dt.date | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Observations up to `last`, oldest first (newest `limit`, or all from `start`), the periods
    Tauric says are missing, and whether the page backstop cut the window short; paged back
    through `coverage.continue_end`."""
    obs: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    end = last.isoformat() if last else None
    since = start.isoformat() if start else None
    for _ in range(_MAX_PAGES):
        want = _PAGE if start else min(_PAGE, limit - len(obs))
        body = await api.get(f"/series/{key}/observations", limit=want, end=end, start=since)
        page = body.get("observations") or []
        obs = page + obs
        cov = body.get("coverage") or {}
        missing += cov.get("missing") or []
        end = cov.get("continue_end")
        if not page or not end or (not start and len(obs) >= limit):
            return obs, missing, False
    return obs, missing, bool(start)


async def _year_ago(api: Tauric, key: str, last: dict[str, Any]) -> dict[str, Any] | None:
    """The observation closest to a year before `last`, read by date. Tauric selects periods
    by their start, so the window opens one period length before the target."""
    period = last["period"]
    latest = dt.date.fromisoformat(period["end"])
    length = latest - dt.date.fromisoformat(period.get("start") or period["end"])
    near = latest - dt.timedelta(days=YEAR_DAYS)
    slack = dt.timedelta(days=YEAR_SLACK_DAYS)
    body = await api.get(
        f"/series/{key}/observations",
        start=(near - length - slack).isoformat(),
        end=(near + slack).isoformat(),
        limit=_PAGE,
    )
    return year_ago(last, body.get("observations") or [])


async def _marketing_years(
    api: Tauric, key: str, req: MacroSeriesRequest
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A marketing-year series comes whole, selected by its year label rather than by date: the
    window is cut here. Returns the window's observations and those up to its end."""
    body = await api.get(f"/series/{key}/observations")
    last = req.end.isoformat() if req.end else None
    upto = [o for o in body.get("observations") or [] if last is None or o["period"]["end"] <= last]
    if req.start:
        return [o for o in upto if o["period"]["start"] >= req.start.isoformat()], upto
    return upto[-req.limit :], upto


async def _projections(api: Tauric, key: str) -> tuple[list[Projection], list[str]]:
    """The series' projections, or a note that it has none."""
    body = await api.get(f"/series/{key}/projections")
    rows = [
        Projection(
            period=p["period"]["label"],
            period_end=p["period"]["end"],
            projection=p["value"],
            known_from=p.get("known_from"),
        )
        for p in body.get("projections") or []
        if p.get("value") is not None
    ]
    return rows, [] if rows else [f"Tauric has no projection for {key}."]


async def _read(api: Tauric, req: MacroSeriesRequest) -> Result:
    key = series_key(req.series or "")
    meta = await api.get(f"/series/{key}")
    s = meta.get("series") or {}
    if s.get("shape") == "bars":
        raise ToolError(
            f"{key} is a priced index: data.market.prices reads it (e.g. ^GSPC, ^VIX, or "
            f"series:{key})"
        )
    by_year_label = s.get("period_kind") == _MARKETING_YEAR
    if by_year_label:
        obs, history = await _marketing_years(api, key, req)
        missing, truncated = [], False
    else:
        # Tauric selects a period by its start, so one can run past the `end` asked for: read
        # one more than wanted
        wanted = req.limit + 1 if req.end and not req.start else req.limit
        obs, missing, truncated = await _observations(api, key, wanted, req.start, req.end)
    last_day = req.end.isoformat() if req.end else None
    rows = [
        o
        for o in obs
        if o.get("value") is not None and (last_day is None or o["period"]["end"] <= last_day)
    ]
    if not rows:
        raise ToolError(f"no observations for {key} in this window")
    if not req.start and not by_year_label:
        rows = rows[-req.limit :]
    unit = s.get("unit") or {}
    last = rows[-1]
    ch = {
        "previous": change(last, rows[-2] if len(rows) > 1 else None, unit),
        "year_ago": change(
            last,
            year_ago(last, history) if by_year_label else await _year_ago(api, key, last),
            unit,
        ),
    }
    first, final = rows[0]["period"]["end"], last["period"]["end"]
    gaps = sorted(
        {
            (m["period"]["end"], m["period"]["label"])
            for m in missing
            if m.get("period") and first <= m["period"]["end"] <= final
        }
    )
    level, moves = value_units(unit)
    projected, notes = await _projections(api, key) if req.projections else ([], [])
    out = MacroSeriesOutput(
        series=key,
        name=s.get("name"),
        unit=unit_text(unit),
        frequency=s.get("frequency"),
        seasonal_adjustment=s.get("seasonal_adjustment"),
        latest=Latest(period=last["period"]["label"], value=last["value"]),
        changes=Changes(**{k: v for k, v in ch.items() if v}),
        truncated=truncated,
        missing=[label for _, label in gaps],
        periods_per_year=periods_per_year(s.get("frequency"), rows),
        dates=[o["period"]["end"] for o in rows],
        # labels that only repeat the date (2024-09 for 2024-09-30) say nothing more
        periods=[]
        if all(o["period"]["end"].startswith(o["period"]["label"]) for o in rows)
        else [o["period"]["label"] for o in rows],
        values=[o["value"] for o in rows],
        projections=projected,
        notes=[*api.limits, *notes],
        units={
            "values": level,
            "latest.value": level,
            "changes.previous.absolute": moves,
            "changes.year_ago.absolute": moves,
        },
    )
    return Result(out, Provenance(**provenance(subject=key, as_of=final)))


async def _handler(req: MacroSeriesRequest) -> Result:
    async with Tauric() as api:
        return await (_read(api, req) if req.series else _find(api, req))


SPEC = ToolSpec(
    name="data_macro_series",
    display_name="data.macro.series",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MacroSeriesRequest,
    handler=_handler,
    output=MacroSeriesOutput,
    tags=("macro", "economics", "series", "inflation", "employment", "rates", "credit", "data"),
)
