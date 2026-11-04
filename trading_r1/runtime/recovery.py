# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Model-call recovery policy: pure decisions, no I/O.

Decides what to do when a model call doesn't cleanly succeed:
  - truncation (``finish_reason == "length"``) -> escalate ``max_tokens``
  - transient (429/5xx/timeout/connection) -> exponential backoff + retry
  - context overflow (400 + the provider's overflow signature) -> compact the prompt + retry
  - malformed generation (other 400) -> regenerate (re-roll, temperature > 0)
  - fatal (401/403/404/bug) -> stop, fail fast

Pure functions of their inputs (exception / budget / messages), so the policy
is table-testable without a live server. Numeric knobs come from config.
"""

from __future__ import annotations

from typing import Any

from trading_r1.providers import Provider
from trading_r1.runtime.evidence import CUT_MARK
from trading_r1.runtime.retry import is_default_retryable, status_of

BACKOFF = "backoff"
COMPACT = "compact"
REGENERATE = "regenerate"
FATAL = "fatal"

# Transient SDK exception class names that carry no HTTP status (the request
# never got a response) and aren't builtin Timeout/Connection subclasses.
_TRANSIENT_EXC_NAMES = frozenset(
    {
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectError",
        "RemoteProtocolError",
    }
)


def _is_transient_by_name(err: BaseException) -> bool:
    return any(t.__name__ in _TRANSIENT_EXC_NAMES for t in type(err).__mro__)


def _message_of(err: BaseException) -> str:
    msg = getattr(err, "message", None)
    if not isinstance(msg, str) or not msg:
        msg = str(err)
    return msg


def is_context_overflow(err: BaseException, provider: Provider) -> bool:
    """True when ``err`` is a prompt-too-long error: an HTTP 400 whose message the provider's
    endpoint words as an overflow."""
    return status_of(err) == 400 and provider.is_overflow(_message_of(err))


def classify_model_error(err: BaseException, provider: Provider) -> str:
    """Map an exception to COMPACT / REGENERATE / BACKOFF / FATAL.

    The 400 split is by kind, not by matching tool-parser strings: a fixed,
    schema-valid request only yields 400s for (a) context overflow -> COMPACT or
    (b) a malformed generation the parser rejected -> REGENERATE (clears on a
    re-roll). Anything else transient -> BACKOFF; otherwise FATAL.
    """
    if is_context_overflow(err, provider):
        return COMPACT
    if status_of(err) == 400:
        return REGENERATE
    if is_default_retryable(err) or _is_transient_by_name(err):
        return BACKOFF
    return FATAL


def escalate_max_tokens(current: int, ceiling: int, factor: int = 2) -> int | None:
    """Next ``max_tokens`` after a truncation, or ``None`` if already at the
    ceiling (accept the best-effort partial)."""
    if current >= ceiling:
        return None
    return min(current * max(factor, 2), ceiling)


def _is_cut(content: str) -> bool:
    return content.startswith(f"[{CUT_MARK}")


def _cut(message: dict[str, Any]) -> dict[str, Any]:
    """The tool message as it stands once cut from the context: an evidence result becomes its
    record's pointer (restorable by id); any other result (a failure, a control tool's reply)
    only says it was cut."""
    content = message.get("content") or ""
    stub = message.get("_pointer") or f"[{CUT_MARK}: {len(content)} chars, not evidence]"
    return {**message, "content": stub}


def _cuttable(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "tool"
        and isinstance(content, str)
        and not _is_cut(content)
        and len(content) > len(message.get("_pointer") or "") + 64
    )


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = 4,
) -> tuple[list[dict[str, Any]], int] | None:
    """Shrink the prompt by cutting old tool-result bodies (the most recent ``keep_recent``
    stay whole) to their pointers. Preserves role + tool_call_id linkage. Returns
    ``(new_messages, n_cut)`` or ``None`` when nothing is left to cut (idempotent). Never
    mutates the input."""
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    protected = set(tool_idxs[-keep_recent:]) if keep_recent > 0 else set()
    cut = [i for i in tool_idxs if i not in protected and _cuttable(messages[i])]
    if not cut:
        return None
    new_messages = [dict(m) for m in messages]
    for i in cut:
        new_messages[i] = _cut(messages[i])
    return new_messages, len(cut)
