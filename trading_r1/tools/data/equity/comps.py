# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.comps: relative valuation against a peer set.

Fetches the trading multiples of a target and a model-supplied peer group
(Tauric profile and filings), takes the peer medians, and applies them to the target's own
metrics to imply a per-share value per multiple (P/E, EV/EBITDA, EV/revenue, P/S). Returns the
peer table, the implied values, and the range vs the current price. The model picks
the peers (it knows the comparables); the tool does the arithmetic.

It also returns per-company fundamentals (market cap, margins, growth, ROE,
leverage) for a comps report's table.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec
from trading_r1.contract.units import Code, Count, Day, Fraction, Money, Multiple, Price, Text
from trading_r1.tools.compute.valuation.comps import compute_comps
from trading_r1.tools.data.common import Symbol, is_num
from trading_r1.tools.data.equity import company, snapshot
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Ticker-driven comparable-company valuation: fetches each peer's multiples and values "
    "the target against their medians. Pass the target as `symbol` and the comparison set as "
    "`peers` (a list of tickers, e.g. ['AMD','AVGO','INTC']). It pulls each company's trading "
    "multiples (P/E, EV/EBITDA, EV/revenue, P/S), takes the peer medians, and applies them "
    "to the target to imply a value per share per multiple, with the range vs the current "
    "price. It also returns each company's fundamentals (market cap, margins, growth, ROE, "
    "net-debt/EBITDA; banks, and a few filers whose trailing-year D&A is not served, have no "
    "EBITDA) for the peer table. For multiples you already have, use "
    "compute.valuation.comps."
)
# Each peer is a live provider fetch: this bounds network fan-out and rate-limit exposure.
_MAX_PEERS = 20

# Descriptive per-company fields a comps report tabulates, from the profile.
_STATS = {
    "market_cap": "market_cap",
    "forward_pe": "forward_pe",
    "operating_margin": "operating_margin",
    "profit_margin": "profit_margin",
    "revenue_growth": "revenue_growth",
    "return_on_equity": "return_on_equity",
}


class EquityCompsRequest(BaseModel):
    """Input for a comparable-company relative valuation."""

    symbol: Symbol = Field(..., description="Target ticker (e.g. NVDA).")
    peers: list[Symbol] = Field(
        ...,
        min_length=1,
        max_length=_MAX_PEERS,
        description="Peer tickers to value against, e.g. ['AMD','AVGO','INTC'].",
    )


class Multiples(BaseModel):
    pe: Multiple = None
    ev_ebitda: Multiple = None
    ev_rev: Multiple = None
    ps: Multiple = None


class Stats(BaseModel):
    """A company's fundamentals for the peer table; amounts in its listing currency."""

    market_cap: Money = None
    forward_pe: Multiple = None
    operating_margin: Fraction = None
    profit_margin: Fraction = None
    revenue_growth: Fraction = None
    return_on_equity: Fraction = None
    net_debt: Money = None
    net_debt_to_ebitda: Multiple = None


class EquityCompsOutput(Output):
    """The target's and each peer's multiples, the peer medians (with how many peers each
    rests on), each company's fundamentals, and the target's value per share implied by each
    median multiple, with their range against the current price."""

    ticker: Code
    valuation_as_of: Day = None  # the target's close that its multiples rest on
    peers: list[Code]
    n_peers: Count
    target_multiples: Multiples
    peer_multiples: dict[str, Multiples]
    peer_medians: dict[str, Multiple]
    peers_per_multiple: dict[str, Count]
    stats: dict[str, Stats]
    implied_per_share: dict[str, Price]
    low: Price
    high: Price
    median_implied: Price
    current_price: Price = None
    upside_to_median: Fraction = None
    skipped: dict[str, Code] = {}  # a multiple with no implied value, and why
    left_out: dict[str, Text] = {}  # a peer that could not be used, and why
    notes: list[Text] = []


def _stats(c: company.Company) -> dict[str, float]:
    """Per-company fundamentals for the peer table."""
    out = {k: c.profile.get(pk) for k, pk in _STATS.items()}
    if c.net_debt is not None:
        out |= snapshot.leverage(
            c.profile.get("total_debt"), c.profile.get("total_cash"), c.ttm.get("ebitda")
        )
    return out


def _target_metrics(c: company.Company) -> dict[str, Any]:
    """The target's metrics for the shared comps core; unavailable ones are None."""
    raw = {
        "eps": c.eps,
        "ebitda": c.ttm.get("ebitda"),
        "revenue": c.ttm.get("revenue"),
        "shares": c.profile.get("shares_outstanding"),
        "net_debt": c.net_debt,
        "price": c.price,
    }
    return {k: (v if is_num(v) else None) for k, v in raw.items()}


async def _handler(req: EquityCompsRequest) -> Result:
    target = req.symbol
    peers = [p for p in dict.fromkeys(req.peers) if p != target]

    async with Tauric() as api:
        names = [target, *peers]
        results = await asyncio.gather(
            *(company.load(api, s) for s in names), return_exceptions=True
        )
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, ToolError):
            raise r  # not a data problem: let it surface
    loaded = dict(zip(names, results, strict=True))
    t = loaded[target]
    if isinstance(t, ToolError):
        raise ToolError(f"no data for target '{target}': {t}")
    left_out = {p: str(e) for p, e in loaded.items() if isinstance(e, ToolError) and p != target}
    peer_cos = {p: c for p, c in loaded.items() if p != target and p not in left_out}
    for p, c in peer_cos.items():
        if c.kind != "stock":
            left_out[p] = f"a {c.kind or 'non-stock'}, not a company"
        elif not c.multiples():
            left_out[p] = "no positive multiple"
    peer_cos = {p: c for p, c in peer_cos.items() if p not in left_out}
    peer_multiples = {p: c.multiples() for p, c in peer_cos.items()}
    if not peer_multiples:
        raise ToolError("no usable peer multiples: check the peer tickers")
    target_multiples = t.multiples()
    stats = {target: _stats(t), **{p: _stats(peer_cos[p]) for p in peer_multiples}}
    res = compute_comps(_target_metrics(t), peer_multiples)
    price, mid = res["current_price"], res["median_implied"]
    out = EquityCompsOutput(
        ticker=target,
        valuation_as_of=t.valuation_date,
        peers=list(peer_multiples),
        target_multiples=Multiples(**target_multiples),
        peer_multiples={p: Multiples(**m) for p, m in peer_multiples.items()},
        peers_per_multiple={
            k: sum(k in m for m in peer_multiples.values()) for k in res["peer_medians"]
        },
        stats={sym: Stats(**s) for sym, s in stats.items()},
        upside_to_median=mid / price - 1 if price and mid else None,
        left_out=left_out,
        notes=[n for c in (t, *peer_cos.values()) for n in c.notes] + api.limits,
        **res,
    )
    return Result(
        out,
        Provenance(
            **provenance(
                subject=f"{target} vs {', '.join(peer_multiples)}",
                as_of=t.valuation_date or t.quote.get("session_date"),
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_comps",
    display_name="data.equity.comps",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=EquityCompsRequest,
    handler=_handler,
    output=EquityCompsOutput,
    tags=("equity", "comps", "relative-valuation", "data"),
)
