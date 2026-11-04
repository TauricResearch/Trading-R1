# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Equity data tools (Tauric): reference, fundamentals, ratios, comps, estimates,
earnings, corporate actions, ownership, SEC filings, digest and thesis."""

from trading_r1.tools.data.equity import (
    comps,
    corporate_actions,
    digest,
    earnings,
    estimates,
    filings,
    fundamentals,
    ownership,
    ratios,
    reference,
)

EQUITY_DATA_SPECS = [
    reference.SPEC,
    fundamentals.SPEC,
    ratios.SPEC,
    comps.SPEC,
    estimates.SPEC,
    earnings.SPEC,
    corporate_actions.SPEC,
    ownership.SPEC,
    filings.SPEC,
    digest.SPEC,
]

__all__ = [
    "EQUITY_DATA_SPECS",
    "comps",
    "corporate_actions",
    "digest",
    "earnings",
    "estimates",
    "filings",
    "fundamentals",
    "ownership",
    "ratios",
    "reference",
]
