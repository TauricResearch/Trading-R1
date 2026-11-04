# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Token usage, metered per run.

Every model response passes through a provider (the Chat Completions seam in `client`, or the
Responses, Gemini or Anthropic provider), and each reports its token counts here. A run opens a
:func:`metered` scope; every call made inside it (the backbone, the page reader, recovery,
wrap-up, the grounding review) adds to that run's meter, keyed by profile: a reader on a profile
of its own (`vllm-reader`) is counted apart from its backbone, one that reads with the
backbone's profile (`tauric`) with it. A call cancelled mid-flight
(a run's timeout) reports nothing. Calls outside a scope
are not counted.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

Usage = dict[str, dict[str, int]]  # profile -> {"calls", "prompt", "completion"}

# the meters open in this context, outermost first: a call is counted in every one
_meter: ContextVar[tuple[Usage, ...]] = ContextVar("tr1_usage", default=())


@contextlib.contextmanager
def metered() -> Iterator[Usage]:
    """Meter the calls made inside the scope; the yielded mapping fills as they return."""
    usage: Usage = {}
    token = _meter.set((*_meter.get(), usage))
    try:
        yield usage
    finally:
        _meter.reset(token)


# the counts of the calls made inside a :func:`captured` scope, one entry per call
_capture: ContextVar[list[dict[str, int]] | None] = ContextVar("tr1_usage_capture", default=None)


@contextlib.contextmanager
def captured() -> Iterator[list[dict[str, int]]]:
    """Collect the token counts of the calls made inside the scope (``prompt_tokens`` and
    ``completion_tokens`` of each call the endpoint reported)."""
    calls: list[dict[str, int]] = []
    token = _capture.set(calls)
    try:
        yield calls
    finally:
        _capture.reset(token)


def record(profile: str, prompt: int | None, completion: int | None) -> None:
    """Add one call's counts to the active meter (a count the endpoint did not report is 0;
    the call is still counted)."""
    if (calls := _capture.get()) is not None and (prompt is not None or completion is not None):
        calls.append({"prompt_tokens": prompt or 0, "completion_tokens": completion or 0})
    for usage in _meter.get():
        row = usage.setdefault(profile, {"calls": 0, "prompt": 0, "completion": 0})
        row["calls"] += 1
        row["prompt"] += prompt or 0
        row["completion"] += completion or 0


def record_openai(profile: str, response: Any) -> None:
    """Counts from an OpenAI-style response or stream chunk (Chat Completions or Responses)."""
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
    completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None)
    record(profile, prompt, completion)
