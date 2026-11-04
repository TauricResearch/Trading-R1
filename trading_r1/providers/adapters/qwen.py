# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Qwen (vLLM) quirk adapter.

Qwen3.x "thinking mode" is enabled per-request via the chat template, and the
HF card recommends ``top_k=20`` alongside the temperature/top_p defaults. vLLM
accepts both through ``extra_body``. Kept off the vanilla path so a hosted OpenAI
call never sends these.
"""

from __future__ import annotations

import copy
from typing import Any

_QWEN_TOP_K = 20


def qwen_adapter(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Inject Qwen's ``top_k`` and ``enable_thinking`` into ``extra_body``
    without clobbering any caller-provided extra_body keys. Pure (returns a
    new dict)."""
    out = copy.deepcopy(kwargs)
    extra_body = dict(out.get("extra_body") or {})
    extra_body.setdefault("top_k", _QWEN_TOP_K)
    chat_template_kwargs = dict(extra_body.get("chat_template_kwargs") or {})
    chat_template_kwargs.setdefault("enable_thinking", True)
    extra_body["chat_template_kwargs"] = chat_template_kwargs
    out["extra_body"] = extra_body
    return out
