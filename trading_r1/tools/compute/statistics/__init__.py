# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Statistics-compute tools: the quant-research descriptive & inferential toolkit."""

from trading_r1.tools.compute.statistics import descriptive, regression

STATISTICS_SPECS = [descriptive.SPEC, regression.SPEC]

__all__ = ["STATISTICS_SPECS", "descriptive", "regression"]
