# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.movers: the session's biggest US stock movers, without names in hand.

Tauric ranks the US listings it serves (common shares and ADRs) by the day's change or share
volume; a quote needs a name first, this finds the names. One list per call, as Tauric ranks it,
with how many stocks it measured and the session it covers: before the open that is the last
session.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Day, Money, Percent, Price, Text
from trading_r1.tools.data.tauric import Tauric, provenance

_MAX_ROWS = 25  # Tauric's most movers per response
_DESCRIPTION = (
    "The session's biggest US stock movers: gainers, losers or the most active by share volume, "
    "ranked by Tauric across the US common shares and ADRs it serves (ETFs are not ranked; "
    "`requested` counts the stocks served, `measured` those ranked, `missing_by_reason` why the "
    "rest were not). One list per call, each row with rank, symbol, name, "
    "price, day change (in percent as published), volume, average volume and market cap. "
    "`session_date` is the session ranked: before the open, the last one. For names already in "
    "hand use data.market.quotes; for sectors, data.market.sectors."
)


class MoversRequest(BaseModel):
    """Input for the movers list."""

    view: Literal["gainers", "losers", "active"] = Field(
        "gainers", description="gainers, losers or active (by share volume)."
    )
    limit: int = Field(10, ge=1, le=_MAX_ROWS, description="Movers to return.")


class Mover(BaseModel):
    rank: Count
    symbol: Code
    name: Code = None
    price: Price
    change: Price = None
    change_pct: Percent = None  # the day's change in percent, as published
    volume: Count = None
    avg_volume: Count = None
    market_cap: Money = None
    currency: Code = None
    market_state: Code = None  # regular, pre_market, post_market, closed


class Missing(BaseModel):
    reason: Code = None
    count: Count


class MoversOutput(Output):
    """One ranked list for a session; `requested` is the stocks Tauric serves, `measured` those
    it ranked, `missing_by_reason` the rest counted by Tauric's reason, and `qualifying` those that
    fit this list."""

    view: Code
    session_date: Day = None
    requested: Count = None
    measured: Count = None
    qualifying: Count = None
    missing_by_reason: list[Missing] = []
    movers: Annotated[list[Mover], hint(rows=_MAX_ROWS)]
    notes: list[Text] = []


def _mover(q: dict[str, Any]) -> Mover:
    inst = q.get("instrument") or {}
    change = q.get("change") or {}
    return Mover(
        rank=q.get("rank"),
        symbol=(inst.get("key") or "").removeprefix("security:"),
        name=inst.get("name"),
        price=q.get("price"),
        change=change.get("absolute"),
        change_pct=change.get("percent"),
        volume=(q.get("day") or {}).get("volume"),
        avg_volume=q.get("avg_volume"),
        market_cap=q.get("market_cap"),
        currency=inst.get("currency"),
        market_state=q.get("market_state"),
    )


async def _handler(req: MoversRequest) -> Result:
    async with Tauric() as api:
        body = await api.get("/markets/equity/movers", type=req.view, limit=req.limit)
    rows = body.get("data") or []
    if not rows:
        raise ToolError(f"Tauric ranks no {req.view} for this session")
    population = body.get("population") or {}
    out = MoversOutput(
        view=req.view,
        session_date=body.get("session_date"),
        requested=population.get("requested"),
        measured=population.get("measured"),
        qualifying=body.get("qualifying"),
        missing_by_reason=population.get("missing_by_reason") or [],
        movers=[_mover(q) for q in rows],
        notes=api.limits,
    )
    return Result(
        out,
        Provenance(**provenance(subject="US stocks", view=req.view, as_of=out.session_date)),
    )


SPEC = ToolSpec(
    name="data_market_movers",
    display_name="data.market.movers",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MoversRequest,
    handler=_handler,
    output=MoversOutput,
    tags=("market", "movers", "gainers", "losers", "active", "equity", "data"),
)
