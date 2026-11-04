# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.risk.correlation: return-correlation matrix across named price series.

Pure-math. Takes a set of named price series (chain them from data.market.prices'
``closes``), converts to returns, aligns to the common window, and returns the
pairwise Pearson correlation matrix. Use to see how names co-move (diversification,
crowding, pair risk).
"""

from __future__ import annotations

import statistics
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Code, Count, Number
from trading_r1.tools.compute.series import ALIGNMENT_NOTE, Prices, align, pct_returns

_DESCRIPTION = (
    "Return-correlation matrix across two or more named price series (a price list each, "
    "data.market.prices "
    "`closes`). Returns the pairwise "
    "Pearson correlations of their returns over the common window. Use for co-movement, "
    "diversification, and pair risk." + ALIGNMENT_NOTE
)
_MIN_POINTS = 3


class CorrelationRequest(BaseModel):
    """Input for a correlation matrix over named price series."""

    series: dict[str, Prices] = Field(
        ...,
        min_length=2,
        description="Map of symbol to price list (>=2 symbols, >=3 prices each), "
        'e.g. {"AAPL": [180, 182, 179], "MSFT": [400, 405, 402]}.',
    )

    @model_validator(mode="after")
    def _check_lengths(self) -> Self:
        short = [k for k, v in self.series.items() if len(v) < _MIN_POINTS]
        if short:
            raise ValueError(f"series need >= {_MIN_POINTS} prices; too short: {short}")
        return self


class CorrelationOutput(Output):
    n_obs: Count
    names: list[Code]
    matrix: dict[str, dict[str, Number]]


def compute_correlation(series: dict[str, list[float]]) -> dict[str, Any]:
    """Compute pairwise correlations from one common aligned return window."""

    names = list(series)
    rets = {k: pct_returns(v) for k, v in series.items()}
    # Align ALL series to one common (most-recent) window, so every pairwise
    # correlation is measured over the same observations (the standard for a
    # correlation matrix) and n_obs is the actual count used.
    aligned = dict(zip(names, align(*(rets[k] for k in names)), strict=True))
    n_obs = len(next(iter(aligned.values()))) if aligned else 0
    matrix: dict[str, dict[str, float | None]] = {a: {} for a in names}
    for i, a in enumerate(names):
        for b in names[i:]:
            ra, rb = aligned[a], aligned[b]
            # correlation is undefined (and statistics.correlation raises) when either
            # series is constant: report None for this valid input.
            computable = n_obs >= 2 and statistics.variance(ra) > 0 and statistics.variance(rb) > 0
            corr = statistics.correlation(ra, rb) if computable else None
            matrix[a][b] = corr
            matrix[b][a] = corr
    return {"names": names, "matrix": matrix, "n_obs": n_obs}


async def _handler(req: CorrelationRequest) -> Result:
    return Result(CorrelationOutput(**compute_correlation(req.series)))


SPEC = ToolSpec(
    name="compute_risk_correlation",
    display_name="compute.risk.correlation",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CorrelationRequest,
    handler=_handler,
    output=CorrelationOutput,
    tags=("compute", "risk", "correlation"),
)
