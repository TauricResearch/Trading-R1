# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.valuation.comps: comparable-company valuation from peer multiples.

The relative-valuation arithmetic, shared with data.equity.comps: take each multiple's peer
median, apply it to the target's matching metric, and convert to a value per share: P/E × EPS;
P/S × revenue / shares; EV/EBITDA and EV/revenue give an EV, less net debt, per share. Reports
the medians, the implied value per multiple, their range and median, and the current price when
given. Multiples ≤ 0 carry no information and are skipped; an implied value ≤ 0 is dropped.
This tool takes peer multiples you supply (from filings, the web, or a curated set); use
data.equity.comps to fetch them for listed tickers.
"""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Multiple, Price

MULTIPLES = ("pe", "ev_ebitda", "ev_rev", "ps")

_DESCRIPTION = (
    "Comparable-company valuation from peer multiples you supply, fetching nothing (for "
    "listed tickers data.equity.comps fetches them). Give peers as a map of peer "
    "name to its multiples (any of pe, ev_ebitda, ev_rev, ps) and the target's eps, ebitda, "
    "revenue, shares, net_debt (EV multiples need net_debt and shares; price optional). Applies "
    "each peer median to the target to "
    "imply a value per share per multiple, with the range and median."
)


def implied_per_share(metric: str, multiple: float, t: dict[str, Any]) -> float | None:
    """Implied value per share from one peer-median multiple and the target's metrics (values
    are numbers or None)."""
    eps, ebitda, rev, shares, net_debt = (
        t.get(k) for k in ("eps", "ebitda", "revenue", "shares", "net_debt")
    )

    def per_share(ev: float) -> float | None:
        return (ev - net_debt) / shares if net_debt is not None and shares else None

    if metric == "pe":
        return multiple * eps if eps is not None else None
    if metric == "ev_ebitda":
        return per_share(multiple * ebitda) if ebitda is not None else None
    if metric == "ev_rev":
        return per_share(multiple * rev) if rev is not None else None
    if metric == "ps":
        return multiple * rev / shares if rev is not None and shares else None
    return None


def compute_comps(
    target: dict[str, Any], peer_multiples: dict[str, dict[str, float]]
) -> dict[str, Any]:
    """Implied values per share and their range, from the peer medians. ``target`` holds the
    target's metrics (numbers or None); ``peer_multiples`` maps each peer to its positive
    multiples."""
    medians = {
        key: statistics.median(vals)
        for key in MULTIPLES
        if (vals := [m[key] for m in peer_multiples.values() if m.get(key, 0) > 0])
    }
    implied = {key: implied_per_share(key, med, target) for key, med in medians.items()}
    skipped = {
        k: "missing target metric" if v is None else "implied value ≤ 0"
        for k, v in implied.items()
        if v is None or v <= 0
    }
    implied = {k: v for k, v in implied.items() if v is not None and v > 0}
    vals = list(implied.values())
    return {
        "peer_medians": medians,
        "implied_per_share": implied,
        "low": min(vals) if vals else None,
        "high": max(vals) if vals else None,
        "median_implied": statistics.median(vals) if vals else None,
        "current_price": target.get("price"),
        "n_peers": len(peer_multiples),
        "skipped": skipped,
    }


class PeerMultiples(BaseModel):
    """One peer's trading multiples (any subset)."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    pe: float | None = None
    ev_ebitda: float | None = None
    ev_rev: float | None = None
    ps: float | None = None


class CompsRequest(BaseModel):
    """Input for a comparable-company valuation from supplied multiples."""

    model_config = ConfigDict(allow_inf_nan=False)

    peers: dict[str, PeerMultiples] = Field(..., min_length=2, max_length=100)
    eps: float | None = Field(None, description="Target EPS (trailing).")
    ebitda: float | None = Field(None, description="Target EBITDA.")
    revenue: float | None = Field(None, description="Target revenue.")
    shares: float | None = Field(None, gt=0, description="Target shares outstanding.")
    net_debt: float | None = Field(None, description="Target net debt (debt − cash).")
    price: float | None = Field(None, gt=0, description="Target current price (optional).")


class CompsOutput(Output):
    """Peer-median multiples (keyed pe, ev_ebitda, ev_rev, ps) and the value per share each
    implies for the target."""

    n_peers: Count
    peer_medians: dict[str, Multiple]
    implied_per_share: dict[str, Price]
    low: Price
    high: Price
    median_implied: Price
    current_price: Price = None
    skipped: dict[str, Code] = {}  # a multiple with no implied value, and why


async def _handler(req: CompsRequest) -> Result:
    target = req.model_dump(exclude={"peers"})
    peers = {name: m.model_dump(exclude_none=True) for name, m in req.peers.items()}
    return Result(CompsOutput(**compute_comps(target, peers)))


SPEC = ToolSpec(
    name="compute_valuation_comps",
    display_name="compute.valuation.comps",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CompsRequest,
    handler=_handler,
    output=CompsOutput,
    tags=("compute", "valuation", "comps", "multiples", "relative-valuation"),
)
