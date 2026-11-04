# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Data tools: tools over Tauric's data API.

Data tools are stateless (unlike the research trio), so they're module-level
``ToolSpec`` constants registered into a registry. Tier is ``deferred``: they're
catalog-only until ``load_tool`` (or a skill) pulls them.
"""
