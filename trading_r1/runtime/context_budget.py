# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Context-budget accounting: pure token estimation + the compaction trigger.

The loop compacts the prompt *before* it overflows, not only after a 400. This is the pure
decision layer (no I/O): estimate a message list's token footprint via a conservative char
heuristic (~4 chars/token + per-message overhead, rounded up so the trigger fires a little
early) and decide whether it is past a fraction of the model's window.
"""

from __future__ import annotations

import json
from typing import Any

from trading_r1.providers import ModelProfile
from trading_r1.state.runtime_config import RecoveryConfig

_CHARS_PER_TOKEN = 4
_PER_MESSAGE_OVERHEAD_TOKENS = 4
_REASONING_ITEMS = frozenset({"thinking", "redacted_thinking", "reasoning"})


def _message_chars(message: dict[str, Any]) -> int:
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += len(content)
    elif content is not None:
        total += len(json.dumps(content, ensure_ascii=False))
    tcs = message.get("tool_calls")
    if tcs:
        total += len(json.dumps(tcs, ensure_ascii=False))
    native = message.get("_native")
    if isinstance(native, dict):  # the provider's reasoning items (the rest repeats the message)
        for item in native.get("items") or ():
            if isinstance(item, dict) and item.get("type") in _REASONING_ITEMS:
                total += len(json.dumps(item, ensure_ascii=False))
    for k in ("name", "tool_call_id"):
        v = message.get(k)
        if isinstance(v, str):
            total += len(v)
    return total


def estimate_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Conservative token estimate (char heuristic + per-message overhead)."""
    if not messages:
        return 0
    chars = sum(_message_chars(m) for m in messages if isinstance(m, dict))
    overhead = _PER_MESSAGE_OVERHEAD_TOKENS * len(messages)
    return (chars + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN + overhead


def schema_tokens(schemas: list[dict[str, Any]] | None) -> int:
    """The tool schemas sent with a request, by the same heuristic."""
    if not schemas:
        return 0
    return (len(json.dumps(schemas, ensure_ascii=False)) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def window_of(profile: ModelProfile, recovery: RecoveryConfig) -> int:
    """The model's context window: the profile's own (stated or discovered), else the default."""
    return profile.context_window or recovery.context_window


def compact_at(profile: ModelProfile, recovery: RecoveryConfig) -> int | None:
    """The prompt size at which a run or session compacts itself, or None when auto-compaction
    is off."""
    if not recovery.auto_compact:
        return None
    return int(window_of(profile, recovery) * recovery.compaction_threshold)


def trigger_tokens(*, window: int, max_tokens: int, fraction: float = 1.0) -> int:
    """The prompt size at which to act: `fraction` of the window, and never so large that
    `max_tokens` of output no longer fits. Floored at 0."""
    return max(0, min(int(window * fraction), window - max_tokens))


def over_budget(
    messages: list[dict[str, Any]] | None,
    *,
    window: int,
    max_tokens: int,
    fraction: float = 1.0,
    tools: list[dict[str, Any]] | None = None,
) -> bool:
    """True when the estimated prompt, with the tool schemas sent beside it, is past the trigger."""
    return estimate_tokens(messages) + schema_tokens(tools) > trigger_tokens(
        window=window, max_tokens=max_tokens, fraction=fraction
    )
