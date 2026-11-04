# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.drawdown: peak-to-trough drawdown episodes of a price or wealth path.

Pure path arithmetic. An episode runs from a high-water mark (peak) down to its low (trough)
and back to a new high (recovery); an unrecovered episode stays open and the series is still
underwater. Reports the maximum drawdown with its timing, the current drawdown from the latest
high, the share of periods spent below a prior high, and the worst episodes: depths and
durations only, no severity labels. The same engine gives `compute.portfolio.performance`
its max drawdown.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, hint
from trading_r1.contract.units import Count, Day, Fraction
from trading_r1.tools.compute.series import Prices, Returns

_DESCRIPTION = (
    "Drawdown analysis of a price path (data.market.prices `closes`, `dates`) or of a return "
    "series (e.g. a portfolio's simple returns): the "
    "maximum drawdown with its peak, trough and recovery dates and durations, the current "
    "drawdown from the latest high, the share of time spent underwater, and the worst "
    "episodes. Depths are negative decimals (−0.35 = a 35% decline). limit default 3. Not for "
    "Sharpe or return statistics (compute.portfolio.performance) or a side-by-side of assets "
    "(compute.portfolio.compare)."
)


class DrawdownRequest(BaseModel):
    """Input for drawdown analysis: closes, or returns compounded into a wealth path."""

    closes: Prices | None = Field(None, min_length=2, description="Prices oldest to newest.")
    returns: Returns | None = Field(
        None, min_length=1, description="Simple per-period returns (instead of closes)."
    )
    dates: list[str] | None = Field(None, description="Dates aligned to closes (optional).")
    limit: int = Field(3, ge=1, le=20, description="How many worst episodes to list.")

    @model_validator(mode="after")
    def _one_input(self) -> DrawdownRequest:
        if (self.closes is None) == (self.returns is None):
            raise ValueError("pass exactly one of closes or returns")
        if self.dates is not None:
            if self.closes is None:
                raise ValueError("dates align to closes; pass closes to use dates")
            if len(self.dates) != len(self.closes):
                raise ValueError("dates must be the same length as closes")
        return self


class Episode(BaseModel):
    """A drawdown from a high-water mark to its low and back; open if not recovered."""

    depth: Fraction
    peak_idx: Count
    trough_idx: Count
    recovery_idx: Count = None
    periods_to_trough: Count
    periods_to_recover: Count = None
    peak_date: Day = None
    trough_date: Day = None
    recovery_date: Day = None


class DrawdownOutput(Output):
    max_drawdown: Fraction
    current_drawdown: Fraction
    underwater: bool
    time_underwater: Fraction
    n_episodes: Count
    n_periods: Count
    # the first of worst_episodes, kept for chaining
    max_drawdown_episode: Annotated[Episode | None, hint(hidden=True)] = None
    worst_episodes: list[Episode]


def wealth_index(returns: list[float]) -> list[float]:
    """Compound simple returns into a level path starting at 1.0."""
    levels = [1.0]
    for r in returns:
        levels.append(levels[-1] * (1 + r))
    return levels


def drawdown_episodes(levels: list[float]) -> tuple[list[dict[str, Any]], float, int]:
    """(episodes, current drawdown, periods below a prior high). Each episode has its depth
    (≤ 0), peak / trough / recovery indices (recovery None if still open) and durations."""
    episodes: list[dict[str, Any]] = []
    peak_i = trough_i = 0
    underwater = 0
    in_dd = False

    def close(recovery_i: int | None) -> None:
        episodes.append(
            {
                "depth": levels[trough_i] / levels[peak_i] - 1,
                "peak_idx": peak_i,
                "trough_idx": trough_i,
                "recovery_idx": recovery_i,
                "periods_to_trough": trough_i - peak_i,
                "periods_to_recover": None if recovery_i is None else recovery_i - trough_i,
            }
        )

    for i, x in enumerate(levels):
        if x >= levels[peak_i]:
            if in_dd:
                close(i)
                in_dd = False
            peak_i = trough_i = i
        else:
            in_dd = True
            underwater += 1
            if x < levels[trough_i]:
                trough_i = i
    if in_dd:
        close(None)
    return episodes, levels[-1] / levels[peak_i] - 1, underwater


def max_drawdown(levels: list[float]) -> float:
    """Largest peak-to-trough decline of a level path (≤ 0)."""
    episodes, _, _ = drawdown_episodes(levels)
    return min((e["depth"] for e in episodes), default=0.0)


def compute_drawdown(req: DrawdownRequest) -> dict[str, Any]:
    levels = req.closes if req.closes is not None else wealth_index(req.returns)
    episodes, current, underwater = drawdown_episodes(levels)
    # A returns path starts at a synthetic 1.0 that is not an observed period.
    n_periods = len(levels) if req.closes is not None else len(req.returns)
    if req.dates is not None:
        for e in episodes:
            for key in ("peak", "trough", "recovery"):
                idx = e[f"{key}_idx"]
                e[f"{key}_date"] = None if idx is None else req.dates[idx]
    worst = sorted(episodes, key=lambda e: e["depth"])[: req.limit]
    return {
        "max_drawdown": worst[0]["depth"] if worst else 0.0,
        "max_drawdown_episode": worst[0] if worst else None,
        "current_drawdown": current,
        "underwater": current < 0,
        "time_underwater": underwater / n_periods,
        "worst_episodes": worst,
        "n_episodes": len(episodes),
        "n_periods": n_periods,
    }


async def _handler(req: DrawdownRequest) -> Result:
    return Result(DrawdownOutput(**compute_drawdown(req)))


SPEC = ToolSpec(
    name="compute_risk_drawdown",
    display_name="compute.risk.drawdown",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DrawdownRequest,
    handler=_handler,
    output=DrawdownOutput,
    tags=("compute", "risk", "drawdown", "path"),
)
