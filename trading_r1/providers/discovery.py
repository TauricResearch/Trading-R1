# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What a model server says about itself: its context window.

A profile states the window when it is known (``ModelProfile.context_window``). Where the server
reports it, its provider asks instead (``Provider.context_window``): a vLLM server in ``/models``
(``max_model_len``), an Ollama server in ``/api/show``, Gemini in its model record. Hosted
OpenAI-compatible APIs report none. A failed question is not an
error: the profile stays as it is and the runtime's default applies.
"""

from __future__ import annotations

import time

from trading_r1.providers import provider_for
from trading_r1.providers.profiles import ModelProfile

_TTL_S = 600.0
_cache: dict[tuple[str | None, str], tuple[float, int | None]] = {}


async def discover_context_window(profile: ModelProfile) -> int | None:
    """The window the server reports for the profile's model, or None when it reports none or
    cannot be asked. Answers are cached for ten minutes."""
    cached = _cache.get((profile.base_url, profile.model))
    if cached and time.monotonic() - cached[0] < _TTL_S:
        return cached[1]
    window: int | None = None
    try:
        window = await provider_for(profile).context_window()
    except Exception:  # noqa: BLE001 (an unanswered question leaves the profile as it is)
        window = None
    _cache[(profile.base_url, profile.model)] = (time.monotonic(), window)
    return window


async def with_context_window(profile: ModelProfile) -> ModelProfile:
    """The profile with its window filled in from the server when it does not state one."""
    if profile.context_window is not None:
        return profile
    window = await discover_context_window(profile)
    return profile if window is None else profile.model_copy(update={"context_window": window})
