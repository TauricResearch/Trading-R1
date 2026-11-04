# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Financial-modeling tools: driver-based projections that feed valuation."""

from trading_r1.tools.compute.modeling import three_statement

MODELING_SPECS = [three_statement.SPEC]

__all__ = ["MODELING_SPECS", "three_statement"]
