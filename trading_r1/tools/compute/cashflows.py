# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Cash-flow math shared by the compute tools: present value and the rate that zeroes it.

Used by ``calc``'s ``npv``/``irr`` and the bond tools' yield solver. A flow list has its first
flow at time 0, the rest one period apart.
"""

from __future__ import annotations

from scipy.optimize import brentq

# The search runs over a rate from -99% to 1000% a period.
IRR_BRACKET = (-0.99, 10.0)


def npv(rate: float, flows: list[float]) -> float:
    """Present value of `flows` at `rate` a period."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))


def irr(flows: list[float]) -> float:
    """The rate a period at which `flows` have a present value of zero."""
    lo, hi = IRR_BRACKET
    if len(flows) < 2 or npv(lo, flows) * npv(hi, flows) > 0:
        raise ValueError(
            f"no IRR between {lo:.0%} and {hi:.0%} a period: the flows need a sign change"
        )
    return brentq(lambda r: npv(r, flows), lo, hi, xtol=1e-14)
