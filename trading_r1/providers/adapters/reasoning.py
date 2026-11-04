# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""OpenAI reasoning-model adapter (the gpt-5 and gpt-6 families via Chat Completions).

Reasoning models reject the classic sampling params: ``max_tokens`` must be
``max_completion_tokens``, and ``temperature`` / ``top_p`` / ``presence_penalty``
only accept their defaults. This adapter performs that translation so a reasoning
profile reuses the same seam as a vanilla one. ``reasoning_effort`` (optional)
rides along via the profile's ``extra_body``. Pure (returns a new dict).
"""

from __future__ import annotations

import copy
from typing import Any

_DROP = ("temperature", "top_p", "presence_penalty")


def reasoning_adapter(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate vanilla chat-completion kwargs for OpenAI reasoning models."""

    out = copy.deepcopy(kwargs)
    if "max_tokens" in out:
        out["max_completion_tokens"] = out.pop("max_tokens")
    for k in _DROP:
        out.pop(k, None)
    return out
