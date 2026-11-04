# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Macro data tools (Tauric): reference series and the category snapshot."""

from trading_r1.tools.data.macro import series, snapshot

MACRO_DATA_SPECS = [series.SPEC, snapshot.SPEC]

__all__ = ["MACRO_DATA_SPECS", "series", "snapshot"]
