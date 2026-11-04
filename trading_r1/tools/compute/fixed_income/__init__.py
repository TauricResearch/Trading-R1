# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Fixed-income-compute tools: bonds, yield curves, and credit spreads and ratios."""

from trading_r1.tools.compute.fixed_income import (
    bond,
    credit_ratios,
    credit_spread,
    horizon_return,
    yield_curve,
)

FIXED_INCOME_SPECS = [
    bond.SPEC,
    yield_curve.SPEC,
    horizon_return.SPEC,
    credit_spread.SPEC,
    credit_ratios.SPEC,
]

__all__ = [
    "FIXED_INCOME_SPECS",
    "bond",
    "credit_ratios",
    "credit_spread",
    "horizon_return",
    "yield_curve",
]
