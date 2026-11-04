# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Compute tools: pure-math, vendor-agnostic analytics.

No network: compute tools consume numbers (often chained from data tools via
`prev:<id>.<path>`) and return derived measures. Stateless deferred SPECs.
"""

from trading_r1.tools.compute.derivatives import DERIVATIVES_SPECS
from trading_r1.tools.compute.fixed_income import FIXED_INCOME_SPECS
from trading_r1.tools.compute.modeling import MODELING_SPECS
from trading_r1.tools.compute.portfolio import PORTFOLIO_SPECS
from trading_r1.tools.compute.risk import RISK_SPECS
from trading_r1.tools.compute.statistics import STATISTICS_SPECS
from trading_r1.tools.compute.technical import TECHNICAL_SPECS
from trading_r1.tools.compute.valuation import VALUATION_SPECS

COMPUTE_SPECS = [
    *VALUATION_SPECS,
    *MODELING_SPECS,
    *RISK_SPECS,
    *DERIVATIVES_SPECS,
    *FIXED_INCOME_SPECS,
    *PORTFOLIO_SPECS,
    *STATISTICS_SPECS,
    *TECHNICAL_SPECS,
]

__all__ = [
    "COMPUTE_SPECS",
    "DERIVATIVES_SPECS",
    "FIXED_INCOME_SPECS",
    "MODELING_SPECS",
    "PORTFOLIO_SPECS",
    "RISK_SPECS",
    "STATISTICS_SPECS",
    "TECHNICAL_SPECS",
    "VALUATION_SPECS",
]
