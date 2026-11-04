# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Risk-compute tools: pure-math risk lenses over price/return series and positions."""

from trading_r1.tools.compute.risk import beta, correlation, drawdown, stress, var, volatility

RISK_SPECS = [correlation.SPEC, var.SPEC, beta.SPEC, volatility.SPEC, drawdown.SPEC, stress.SPEC]

__all__ = ["RISK_SPECS", "beta", "correlation", "drawdown", "stress", "var", "volatility"]
