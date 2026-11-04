# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The tools: research (search/visit/cite) + equity/market data + valuation, risk,
portfolio, fixed-income, derivatives, and statistics compute.

Tools that share per-run state (the research trio share a FetchCache) are built
per request via factory functions that close over that state, then registered
into a fresh registry, so each request is isolated.
"""
