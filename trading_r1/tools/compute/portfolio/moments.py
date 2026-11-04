# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Shared portfolio math: aligned return matrix, annualized moments, risk contributions.

Portfolio aggregation uses simple returns (a fixed-weight portfolio's period return is
Σ wᵢ*rᵢ; log returns do not aggregate across holdings). Series are aligned to their most
recent common window, as in `compute.risk.correlation`.
"""

from __future__ import annotations

from typing import Annotated

import numpy as np
from pydantic import Field

from trading_r1.tools.compute.series import Prices, align, pct_returns

# Named price series for a portfolio: 2-50 assets, each at least 3 prices.
NamedPrices = Annotated[
    dict[str, Annotated[Prices, Field(min_length=3)]], Field(min_length=2, max_length=50)
]


def return_matrix(prices: dict[str, list[float]]) -> tuple[list[str], np.ndarray]:
    """(names, R) with R of shape (assets, periods): aligned simple returns."""
    names = list(prices)
    rows = align(*(pct_returns(prices[k]) for k in names))
    return names, np.asarray(rows, dtype=float)


def annualized_moments(r: np.ndarray, periods_per_year: float) -> tuple[np.ndarray, np.ndarray]:
    """(μ, Σ): annualized mean returns and sample covariance (ddof=1)."""
    return r.mean(axis=1) * periods_per_year, np.atleast_2d(np.cov(r, ddof=1)) * periods_per_year


def risk_contributions(w: np.ndarray, cov: np.ndarray) -> np.ndarray | None:
    """Each holding's share of portfolio variance, wᵢ*(Σw)ᵢ / wᵀΣw (sums to 1); None if the
    portfolio has zero variance."""
    var = float(w @ cov @ w)
    return None if var <= 0 else w * (cov @ w) / var
