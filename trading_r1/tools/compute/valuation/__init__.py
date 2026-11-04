# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Valuation-compute tools: DCF, DCF sensitivity, WACC, comps and LBO returns."""

from trading_r1.tools.compute.valuation import comps, dcf, dcf_sensitivity, lbo, wacc

VALUATION_SPECS = [wacc.SPEC, dcf.SPEC, dcf_sensitivity.SPEC, comps.SPEC, lbo.SPEC]

__all__ = ["VALUATION_SPECS", "comps", "dcf", "dcf_sensitivity", "lbo", "wacc"]
