# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""compute.fixed_income.credit_spread: credit spread and market-implied default probability.

The market lens on credit risk. From a bond's yield and a matched-maturity government yield:
the spread (in decimal and basis points), and the default intensity it implies under the
reduced-form credit triangle, hazard ≈ spread / LGD with LGD = 1 − recovery, and the
cumulative default probability over a maturity T, 1 − e^{−hazard*T}. A spread also carries
credit-risk and liquidity premia beyond expected loss, so the implied probability is
risk-neutral: an upper bound on the real-world default rate. A negative spread implies none.
40% recovery is the market convention for senior unsecured debt: an assumption, not an
estimate of the issuer's recovery.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Fraction, Number

_DESCRIPTION = (
    "Credit spread and market-implied default probability. Give the bond's yield (decimal, e.g. "
    "from compute.fixed_income.bond's ytm) and the government yield at the same maturity ("
    "compute.fixed_income.yield_curve's queried `yield`, the observed-curve yield, not the zero "
    "rate), which makes this the G-spread; optionally recovery_rate (default "
    "0.40, the senior-unsecured convention) and maturity_years. Returns the spread in bp, the "
    "implied annual default intensity (spread / LGD) and the cumulative default probability "
    "over the maturity: risk-neutral, so an upper bound on the real-world rate."
)


class CreditSpreadRequest(BaseModel):
    """Input for a credit spread and its implied default probability."""

    model_config = ConfigDict(allow_inf_nan=False)

    bond_yield: float = Field(..., gt=-0.1, lt=1, description="The bond's yield, decimal p.a.")
    benchmark_yield: float = Field(
        ..., gt=-0.1, lt=1, description="Matched-maturity government yield, decimal p.a."
    )
    recovery_rate: float = Field(
        0.40, ge=0, lt=1, description="Recovery on default (LGD = 1 − it)."
    )
    maturity_years: float | None = Field(None, gt=0, le=100, description="Years to maturity.")


class CreditSpreadOutput(Output):
    spread: Fraction
    spread_bp: Number
    bond_yield: Fraction
    benchmark_yield: Fraction
    recovery_rate: Fraction
    loss_given_default: Fraction
    # risk-neutral (an upper bound on the real-world rate); n/a for a negative spread
    implied_annual_default_intensity: Fraction
    implied_cumulative_default_probability: Fraction = None  # with maturity_years
    maturity_years: Number = None


def compute_credit_spread(req: CreditSpreadRequest) -> dict[str, Any]:
    spread = req.bond_yield - req.benchmark_yield
    lgd = 1 - req.recovery_rate
    hazard = spread / lgd if spread >= 0 else None
    return {
        "spread": spread,
        "spread_bp": spread * 10_000,
        "bond_yield": req.bond_yield,
        "benchmark_yield": req.benchmark_yield,
        "recovery_rate": req.recovery_rate,
        "loss_given_default": lgd,
        "implied_annual_default_intensity": hazard,
        "implied_cumulative_default_probability": (
            1 - math.exp(-hazard * req.maturity_years)
            if hazard is not None and req.maturity_years is not None
            else None
        ),
        "maturity_years": req.maturity_years,
    }


async def _handler(req: CreditSpreadRequest) -> Result:
    return Result(CreditSpreadOutput(**compute_credit_spread(req)))


SPEC = ToolSpec(
    name="compute_fixed_income_credit_spread",
    display_name="compute.fixed_income.credit_spread",
    result_kind="compute",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=CreditSpreadRequest,
    handler=_handler,
    output=CreditSpreadOutput,
    tags=("compute", "fixed_income", "credit", "spread", "default-probability"),
)
