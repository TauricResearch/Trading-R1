# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Portfolio-compute tools: performance, comparison, decomposition, optimization, rebalancing."""

from trading_r1.tools.compute.portfolio import (
    compare,
    decomposition,
    optimization,
    performance,
    rebalance,
)

PORTFOLIO_SPECS = [
    performance.SPEC,
    compare.SPEC,
    decomposition.SPEC,
    optimization.SPEC,
    rebalance.SPEC,
]

__all__ = [
    "PORTFOLIO_SPECS",
    "compare",
    "decomposition",
    "optimization",
    "performance",
    "rebalance",
]
