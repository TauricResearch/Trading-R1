# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.positioning: who is long and short a futures market (CFTC Commitments of
Traders).

The CFTC's weekly report for a futures root, as Tauric serves it: financial futures (equity
indices, rates, currencies) in the Traders in Financial Futures split (dealers, asset managers,
leveraged funds, other reportables) and commodities in the disaggregated split (producers and
merchants, swap dealers, managed money, other reportables); non-reportable positions beside. Per
group: long, short, spread and net contracts, net as a share of open interest, the change in net
over one, 4, 13 and 26 weeks, and the COT index (where the latest net sits between the window's
lowest and highest net, 0-100). Positions are as of each Tuesday, published the following Friday.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Day, Fraction, Number, Text
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.symbols import resolve
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "CFTC Commitments of Traders for a futures root (CL crude, GC gold, NG, ZC corn, ES S&P "
    "500, ZN 10-year note, 6E euro, …; CL=F also works): for each trader group (dealers, "
    "asset managers, leveraged funds (financial futures) or producers/merchants, swap dealers, "
    "managed money (commodities), plus other and non-reportable): long, short and net "
    "contracts, net as a share of open interest, the change in net over one week, 4, 13 and 26 "
    "weeks (`net_change_from` dates each), and the COT index "
    "(0-100: where the latest net sits between the window's low and high). `weeks` of history "
    "(oldest first: `dates`, and `net_history` per group aligned with them); `end` reads the "
    "report on or before a past date, with its window behind it. As of each Tuesday, published "
    "Friday."
)
_PAGE = 33  # Tauric's most reports per response
_PERCENT = 100
_CHANGE_WEEKS = (4, 13, 26)


class PositioningRequest(BaseModel):
    """Input for the positioning report."""

    symbol: Symbol = Field(..., description="Futures root, e.g. CL, GC, ES, ZN.")
    weeks: int = Field(52, ge=2, le=156, description="Weekly reports to read (the COT window).")
    end: dt.date | None = Field(
        None, description="Read back from the report on or before this date (default: the latest)."
    )


async def _reports(
    api: Tauric, path: str, weeks: int, last: dt.date | None
) -> tuple[dict[str, Any], list[dict]]:
    """Reports up to `last`, oldest first, paged back through `coverage.continue_end`."""
    body = await api.get(path, reports=min(weeks, _PAGE), end=last.isoformat() if last else None)
    first, reports = body, list(body.get("reports") or [])
    end = (body.get("coverage") or {}).get("continue_end")
    while end and len(reports) < weeks:
        body = await api.get(path, reports=min(weeks - len(reports), _PAGE), end=end)
        page = body.get("reports") or []
        if not page:
            break
        reports += page
        end = (body.get("coverage") or {}).get("continue_end")
    return first, sorted(reports, key=lambda r: r["period"]["end"])


def cot_index(nets: list[float]) -> float | None:
    """Where the latest net sits between the window's low and high, 0-100."""
    lo, hi = min(nets), max(nets)
    return None if hi == lo else (nets[-1] - lo) / (hi - lo) * _PERCENT


def _weeks_back(dates: list[str]) -> dict[int, int]:
    """For each change window the dates reach, the index of the last report on or before the date
    that far back."""
    latest = dt.date.fromisoformat(dates[-1])
    out = {}
    for weeks in _CHANGE_WEEKS:
        target = (latest - dt.timedelta(weeks=weeks)).isoformat()
        if dates[0] <= target:
            out[weeks] = max(i for i, d in enumerate(dates) if d <= target)
    return out


def groups(reports: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each trader group's latest position, its change in net over the change windows, and its net
    over the window (aligned with the reports' dates; None where a week lacks the group)."""
    latest = reports[-1]
    back = _weeks_back([r["period"]["end"] for r in reports])
    oi = latest.get("open_interest")
    out = {}
    for name, pos in (latest.get("positions") or {}).items():
        nets = [((r.get("positions") or {}).get(name) or {}).get("net") for r in reports]
        net = pos.get("net")
        known = [n for n in nets if n is not None]
        prior = nets[-2] if len(nets) > 1 else None
        out[name] = {
            "long": pos.get("long"),
            "short": pos.get("short"),
            "spread": pos.get("spread"),
            "net": net,
            "net_of_open_interest": net / oi if oi and net is not None else None,
            "net_change": net - prior if net is not None and prior is not None else None,
            **{
                f"net_change_{weeks}w": net - nets[i]
                for weeks, i in back.items()
                if net is not None and nets[i] is not None
            },
            "cot_index": cot_index(known) if net is not None and known else None,
            "net_history": nets,
        }
    return out


class Group(BaseModel):
    long: Count = None
    short: Count = None
    spread: Count = None
    net: Count = None
    net_of_open_interest: Fraction = None
    net_change: Count = None  # the week's change in net
    net_change_4w: Count = None
    net_change_13w: Count = None
    net_change_26w: Count = None
    cot_index: Number = None  # 0-100: the latest net between the window's low and high
    net_history: Annotated[list[Count], hint(hidden=True)]  # aligned with `dates`


class PositioningOutput(Output):
    """The latest report's positions by trader group, over a window of `weeks` reports from
    `start` (`dates`, oldest first, align each group's `net_history`)."""

    root: Code
    market: Code = None
    report: Code = None
    as_of: Day
    open_interest: Count = None
    weeks: Count
    start: Day
    net_change_from: dict[str, Day]  # the report each change in net runs from
    dates: Annotated[list[Day], hint(hidden=True)]
    groups: dict[str, Group]
    notes: list[Text] = []


async def _handler(req: PositioningRequest) -> Result:
    inst = resolve(req.symbol, "future")
    root = inst.key.removeprefix("future:").removesuffix("/continuous")
    async with Tauric() as api:
        first, reports = await _reports(api, f"/futures/{root}/positioning", req.weeks, req.end)
    if not reports:
        raise ToolError(f"no CFTC positioning for {root}")
    g = groups(reports)
    latest = reports[-1]
    market = first.get("market") or {}
    dates = [r["period"]["end"] for r in reports]
    out = PositioningOutput(
        root=root,
        market=latest.get("market_name"),
        report=market.get("report"),
        as_of=dates[-1],
        open_interest=latest.get("open_interest"),
        weeks=len(reports),
        start=dates[0],
        net_change_from={f"{w}w": dates[i] for w, i in _weeks_back(dates).items()},
        dates=dates,
        groups=g,
        notes=api.limits,
    )
    return Result(out, Provenance(**provenance(subject=root, as_of=out.as_of, source="CFTC")))


SPEC = ToolSpec(
    name="data_market_positioning",
    display_name="data.market.positioning",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=PositioningRequest,
    handler=_handler,
    output=PositioningOutput,
    tags=("market", "futures", "positioning", "cot", "cftc", "data"),
)
