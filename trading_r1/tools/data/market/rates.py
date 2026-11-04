# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.rates: the US Treasury par yield curve (the risk-free backdrop).

Tauric's constant-maturity curve: eleven tenors, 1 month to 30 years, on a bond-equivalent basis,
for the latest day, any past date since 1962, or a window of daily history with each tenor's change
and the volatility of its daily changes in basis points (normal vol, the rates convention;
annualized ×√252). Two decimal risk-free rates for the two conventions: the 10-year
(`risk_free_rate`, CAPM/WACC discounting) and the 3-month bill (`t_bill_rate`, Sharpe and excess
returns). `curve` is chain-ready for compute.fixed_income.yield_curve; `spreads_bp` gives the
conventional slope measures, named as that tool names them. Numbers only.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import statistics
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from trading_r1.contract.spec import (
    Output,
    Provenance,
    Result,
    ToolError,
    ToolSpec,
    hint,
    rendered,
)
from trading_r1.contract.units import Code, Day, Fraction, Number, Percent, Text
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "US Treasury par yield curve: the constant-maturity tenors 1m, 3m, 6m, 1y, 2y, 3y, 5y, 7y, "
    "10y, 20y and 30y (no 1.5m, 2m or 4m), slope spreads in basis points, and two decimal "
    "risk-free rates: risk_free_rate, the 10-year (CAPM/WACC discounting), and t_bill_rate, the "
    "3-month bill (Sharpe / excess returns, as risk_free_annual). All are bond-equivalent "
    "(semiannual) yields. The latest curve is usually a business day or more behind today; `as_of` "
    "is its date. `curve` lists (maturity, decimal rate) points for "
    "compute.fixed_income.yield_curve, the published tenors only; for a maturity between "
    "them (6y, 4.5y) interpolate there with query_maturities rather than reusing a neighbouring "
    "tenor. Optional date (YYYY-MM-DD, from 1962) gives the curve on or before that day; "
    "days > 1 adds daily `history`, `change_bp` per tenor over the window and `change_vol_bp`. "
    "Tenor sets differ by era (only 1y-20y in 1962, no 30y 2002-2006)."
)
_SOURCE = "Tauric constant-maturity yield curve"
_FIRST_DATE = dt.date(1962, 1, 2)
_YEARS = {
    "1m": 1 / 12,
    "3m": 0.25,
    "6m": 0.5,
    "1y": 1.0,
    "2y": 2.0,
    "3y": 3.0,
    "5y": 5.0,
    "7y": 7.0,
    "10y": 10.0,
    "20y": 20.0,
    "30y": 30.0,
}  # tenor to years to maturity, in maturity order
_PAGE = 110  # Tauric's most observations per response
_MAX_DAYS = 260  # about a year of business days
_BP_PER_PCT = 100
_BUSINESS_DAYS_PER_YEAR = 252  # annualizes the volatility of daily yield changes
_MIN_CHANGES_FOR_VOL = 2  # a sample standard deviation needs two observations
# (name, long tenor, short tenor): the names compute.fixed_income.yield_curve uses
_SPREADS = (
    ("10y_minus_2y", "10y", "2y"),
    ("10y_minus_3m", "10y", "3m"),
    ("30y_minus_5y", "30y", "5y"),
)


class MarketRatesRequest(BaseModel):
    """Input for the Treasury yield curve."""

    date: dt.date | None = Field(
        None, description="Curve on or before this date (YYYY-MM-DD); default the latest."
    )
    days: int = Field(1, ge=1, le=_MAX_DAYS, description="Daily curves to return (business days).")

    @field_validator("date")
    @classmethod
    def _date(cls, v: dt.date | None) -> dt.date | None:
        if v is not None and v < _FIRST_DATE:
            raise ValueError(f"the par curve begins {_FIRST_DATE}")
        return v


class CurvePoint(BaseModel):
    maturity: Number  # years
    rate: Fraction


class History(BaseModel):
    """Daily curves, oldest first: each tenor's yields aligned with `dates` (null on a day the
    tenor was not published)."""

    model_config = rendered(index="dates")

    dates: list[Day]
    tenors_pct: dict[str, list[Percent]]


class ChangeVol(BaseModel):
    daily: Number  # bp
    annualized: Number  # bp, ×√252


class RatesOutput(Output):
    """The curve on its date: yields in percent as published, the two risk-free rates as
    decimals, the slope spreads in bp; with days > 1, the window's history and moves."""

    source: Code
    as_of: Day
    tenors_pct: dict[str, Percent]
    ten_year_pct: Percent = None
    risk_free_rate: Fraction = None  # the 10-year: CAPM/WACC discounting
    t_bill_rate: Fraction = None  # the 3-month bill: Sharpe, excess returns
    spreads_bp: dict[str, Number]
    curve: Annotated[list[CurvePoint], hint(hidden=True)]  # tenors_pct as decimal points
    history_start: Day = None
    change_bp: dict[str, Number] = {}
    change_start: dict[str, Day] = {}  # tenors first published inside the window
    change_vol_bp: Annotated[dict[str, ChangeVol], hint(rows=20)] = {}  # every tenor
    notes: list[Text] = []
    history: History | None = None


async def _curve(api: Tauric, date: dt.date | None) -> tuple[dict[str, Any], dict[str, str]]:
    """The curve on or before ``date``, and the series behind each of its tenors."""
    body = await api.get(
        "/markets/rates/yield-curve", date=date.isoformat() if date is not None else None
    )
    points = {
        p["maturity"].lower(): p
        for p in body.get("data") or []
        if (p.get("latest") or {}).get("value") is not None and p["maturity"].lower() in _YEARS
    }
    if not points:
        raise ToolError("no Treasury yield data available for that date")
    tenors = {k: points[k]["latest"]["value"] for k in _YEARS if k in points}
    # a tenor not published on this date can still have history inside a window ending here
    absent = {
        str(m["label"]).lower(): str(m["key"])
        for m in body.get("missing") or []
        if str(m.get("label", "")).lower() in _YEARS
    }
    series = {k: str(points[k]["series"]) for k in tenors} | absent
    series = {k: key.removeprefix("series:") for k, key in series.items()}
    return {"date": body["curve_date"], "tenors_pct": tenors}, series


async def _tenor_history(api: Tauric, key: str, end: str, days: int) -> list[dict[str, Any]]:
    """The newest ``days`` observations of one tenor's series on or before ``end``."""
    found: list[dict[str, Any]] = []
    cursor: str | None = end
    while cursor and len(found) < days:
        body = await api.get(
            f"/series/{key}/observations", end=cursor, limit=min(_PAGE, days - len(found))
        )
        page = body.get("observations") or []
        if not page:
            break
        found = page + found
        cursor = (body.get("coverage") or {}).get("continue_end")
    return found


async def _window(
    api: Tauric, latest: dict[str, Any], series: dict[str, str], days: int
) -> list[dict[str, Any]]:
    """The ``days`` most recent daily curves up to ``latest``, newest first."""
    columns = await asyncio.gather(
        *(_tenor_history(api, key, latest["date"], days) for key in series.values())
    )
    by_day: dict[str, dict[str, float]] = {latest["date"]: dict(latest["tenors_pct"])}
    for label, column in zip(series, columns, strict=True):
        for obs in column:
            if obs.get("value") is not None:
                by_day.setdefault(obs["period"]["end"], {})[label] = obs["value"]
    newest = sorted(by_day, reverse=True)[:days]
    return [
        {"date": d, "tenors_pct": {k: by_day[d][k] for k in _YEARS if k in by_day[d]}}
        for d in newest
    ]


def _decimal(pct: float | None) -> float | None:
    return None if pct is None else pct / 100


def window_stats(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Per tenor over an oldest-first window: the change from its first observation (dated when
    the tenor starts later than the window) and the volatility of its daily changes, in bp."""
    first: dict[str, tuple[str, float]] = {}
    moves: dict[str, list[float]] = {}
    prev: dict[str, float] = {}
    for row in history:
        for label, pct in row["tenors_pct"].items():
            first.setdefault(label, (row["date"], pct))
            if label in prev:
                moves.setdefault(label, []).append((pct - prev[label]) * _BP_PER_PCT)
            prev[label] = pct
    latest = history[-1]["tenors_pct"]
    change = {
        label: (latest[label] - first[label][1]) * _BP_PER_PCT for label in latest if label in first
    }
    late = {label: first[label][0] for label in change if first[label][0] != history[0]["date"]}
    vol = {
        label: {"daily": sd, "annualized": sd * math.sqrt(_BUSINESS_DAYS_PER_YEAR)}
        for label in latest  # maturity order
        if len(moves.get(label, [])) >= _MIN_CHANGES_FOR_VOL
        for sd in [statistics.stdev(moves[label])]
    }
    return {"change_bp": change, "change_start": late, "change_vol_bp": vol}


def _history(rows: list[dict[str, Any]]) -> History:
    """Oldest-first rows as aligned columns, tenors in maturity order."""
    labels = sorted({k for r in rows for k in r["tenors_pct"]}, key=_YEARS.__getitem__)
    return History(
        dates=[r["date"] for r in rows],
        tenors_pct={k: [r["tenors_pct"].get(k) for r in rows] for k in labels},
    )


def curve_response(
    rows: list[dict[str, Any]],
    source: str,
    *,
    requested_days: int = 1,
    limits: list[str] | None = None,
) -> Result:
    """``rows`` newest first."""
    tenors = rows[0]["tenors_pct"]
    fields: dict[str, Any] = {
        "source": source,
        "as_of": rows[0]["date"],
        "tenors_pct": tenors,
        "ten_year_pct": tenors.get("10y"),
        # decimal forms for chaining (4.45% -> 0.0445): 10y for discounting, 3m for Sharpe
        "risk_free_rate": _decimal(tenors.get("10y")),
        "t_bill_rate": _decimal(tenors.get("3m")),
        "curve": [
            {"maturity": _YEARS[label], "rate": _decimal(pct)} for label, pct in tenors.items()
        ],
        "spreads_bp": {
            name: (tenors[long] - tenors[short]) * _BP_PER_PCT
            for name, long, short in _SPREADS
            if long in tenors and short in tenors
        },
    }
    if len(rows) > 1:
        history = rows[::-1]  # oldest first, like every chainable series
        fields |= {"history": _history(history), "history_start": history[0]["date"]}
        fields |= window_stats(history)
    fields["notes"] = list(limits or [])
    if len(rows) < requested_days:
        fields["notes"].append(
            f"Only {len(rows)} of {requested_days} requested curves exist (the series begins "
            f"{_FIRST_DATE})."
        )
    meta = provenance(subject="US Treasury curve", as_of=rows[0]["date"], source=source)
    return Result(RatesOutput(**fields), Provenance(**meta))


async def _handler(req: MarketRatesRequest) -> Result:
    async with Tauric() as api:
        latest, series = await _curve(api, req.date)
        rows = [latest] if req.days == 1 else await _window(api, latest, series, req.days)
    return curve_response(rows or [latest], _SOURCE, requested_days=req.days, limits=api.limits)


SPEC = ToolSpec(
    name="data_market_rates",
    display_name="data.market.rates",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MarketRatesRequest,
    handler=_handler,
    output=RatesOutput,
    tags=("market", "rates", "treasury", "risk-free", "yield-curve", "data"),
)
