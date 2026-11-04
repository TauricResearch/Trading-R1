# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.portfolio.rebalance: the trades that move current holdings to target weights.

Allocation arithmetic: total = Σ current values; target valueᵢ = target weightᵢ * total;
tradeᵢ = target valueᵢ − current valueᵢ (+ buy, − sell); in shares too when a price is given;
driftᵢ = current weightᵢ − target weightᵢ. Holdings absent from the targets are sold to
zero; target names not held are new buys. Target weights summing below 1 leave the
remainder in cash. Reports one-way turnover (half the gross traded value over total).
No drift thresholds or advice on whether to rebalance.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Fraction, Money

_DESCRIPTION = (
    "Trades to rebalance current holdings to target weights. Pass holdings as a map of name "
    "to current market value, targets as a map of name to weight (summing to at most 1; any "
    "remainder stays in cash), and optionally prices as a map of name to price to size trades in "
    "shares. Returns each name's current and target weight, drift, the trade (+ buy / − "
    "sell) and the one-way turnover."
)


class RebalanceRequest(BaseModel):
    """Input for a rebalance to target weights."""

    model_config = ConfigDict(allow_inf_nan=False)

    holdings: dict[str, float] = Field(
        ..., min_length=1, max_length=500, description="Map of name to current market value (≥ 0)."
    )
    targets: dict[str, float] = Field(
        ..., min_length=1, max_length=500, description="Map of name to target weight (0-1)."
    )
    prices: dict[str, float] | None = Field(
        None, description="Map of name to current price, to express trades in shares (optional)."
    )

    @model_validator(mode="after")
    def _check(self) -> RebalanceRequest:
        if any(v < 0 for v in self.holdings.values()):
            raise ValueError("holding values must be ≥ 0")
        if sum(self.holdings.values()) <= 0:
            raise ValueError("holdings have no value to rebalance")
        if any(not 0 <= w <= 1 for w in self.targets.values()):
            raise ValueError("target weights must be between 0 and 1")
        if math.fsum(self.targets.values()) > 1:  # fsum: 0.1 + 0.2 + 0.7 is exactly 1
            raise ValueError("target weights sum above 1")
        if self.prices:
            if any(p <= 0 for p in self.prices.values()):
                raise ValueError("prices must be > 0")
            unknown = set(self.prices) - set(self.holdings) - set(self.targets)
            if unknown:
                raise ValueError(
                    f"prices name holdings not in holdings or targets: {sorted(unknown)}"
                )
        return self


class Position(BaseModel):
    name: Code
    current_value: Money
    current_weight: Fraction
    target_weight: Fraction
    drift: Fraction
    trade_value: Money
    action: Code
    trade_shares: Count = None  # with a price


class RebalanceOutput(Output):
    total_value: Money
    cash_target_weight: Fraction
    cash_target_value: Money
    positions: list[Position]
    gross_trade_value: Money
    turnover_one_way: Fraction
    n_buys: Count
    n_sells: Count


# A trade smaller than this share of the book is arithmetic noise, not a trade.
_NO_TRADE = 1e-9


def compute_rebalance(req: RebalanceRequest) -> dict[str, Any]:
    total = sum(req.holdings.values())
    prices = req.prices or {}
    rows = []
    for name in sorted(set(req.holdings) | set(req.targets)):
        current = req.holdings.get(name, 0.0)
        target_w = req.targets.get(name, 0.0)
        trade = target_w * total - current
        if abs(trade) < _NO_TRADE * total:  # float noise of a position already at target
            trade = 0.0
        row = {
            "name": name,
            "current_value": current,
            "current_weight": current / total,
            "target_weight": target_w,
            "drift": current / total - target_w,
            "trade_value": trade,
            "action": "buy" if trade > 0 else "sell" if trade < 0 else "hold",
        }
        if name in prices:
            row["trade_shares"] = trade / prices[name]
        rows.append(row)
    gross = math.fsum(abs(r["trade_value"]) for r in rows)
    cash_weight = max(0.0, 1.0 - math.fsum(req.targets.values()))
    return {
        "total_value": total,
        "cash_target_weight": cash_weight,
        "cash_target_value": cash_weight * total,
        "positions": rows,
        "gross_trade_value": gross,
        "turnover_one_way": gross / 2 / total,
        "n_buys": sum(r["action"] == "buy" for r in rows),
        "n_sells": sum(r["action"] == "sell" for r in rows),
    }


async def _handler(req: RebalanceRequest) -> Result:
    return Result(RebalanceOutput(**compute_rebalance(req)))


SPEC = ToolSpec(
    name="compute_portfolio_rebalance",
    display_name="compute.portfolio.rebalance",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=RebalanceRequest,
    handler=_handler,
    output=RebalanceOutput,
    tags=("compute", "portfolio", "rebalance", "trades"),
)
