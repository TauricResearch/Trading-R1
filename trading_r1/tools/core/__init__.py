# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Core meta-tools: load_tool + load_skill (two-tier disclosure) + ask_user (clarify) +
evidence (read back recorded results) + remember/forget (user memory, chat sessions only).

Built per run as closures over the run's state: the loaders over the session state
(so they mutate the same state the loop reads for `visible_schemas`), ask_user over the
run's elicitor (emit + timeout).
"""

from trading_r1.tools.core.ask_user import build_ask_user_spec
from trading_r1.tools.core.evidence import build_evidence_spec
from trading_r1.tools.core.load_skill import build_load_skill_spec
from trading_r1.tools.core.load_tool import build_load_tool_spec
from trading_r1.tools.core.memory import build_memory_specs

__all__ = [
    "build_ask_user_spec",
    "build_evidence_spec",
    "build_load_skill_spec",
    "build_load_tool_spec",
    "build_memory_specs",
]
