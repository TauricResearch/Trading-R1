# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Technical-analysis tools: pure-math indicators over a price series."""

from trading_r1.tools.compute.technical import bollinger, macd, momentum, moving_average, rsi

TECHNICAL_SPECS = [rsi.SPEC, macd.SPEC, bollinger.SPEC, moving_average.SPEC, momentum.SPEC]

__all__ = ["TECHNICAL_SPECS", "bollinger", "macd", "momentum", "moving_average", "rsi"]
