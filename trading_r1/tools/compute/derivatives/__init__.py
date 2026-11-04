# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Derivatives-compute tools: pure-math option pricing, implied volatility and position Greeks."""

from trading_r1.tools.compute.derivatives import (
    binomial,
    black_scholes,
    implied_vol,
    position_greeks,
)

DERIVATIVES_SPECS = [black_scholes.SPEC, binomial.SPEC, implied_vol.SPEC, position_greeks.SPEC]

__all__ = ["DERIVATIVES_SPECS", "binomial", "black_scholes", "implied_vol", "position_greeks"]
