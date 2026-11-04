# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Retry helper for tool handlers + the shared transient-error taxonomy.

Wraps an async-callable factory; absorbs transient failures (timeouts, transport
blips, rate limits, transient 5xx) before they reach the LLM. Actionable errors
(bad ticker, schema mismatch, auth) bubble through immediately.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableError(Exception):
    """Explicit caller signal that an error is transient."""


def status_of(err: BaseException) -> int | None:
    """HTTP status from an SDK exception, across provider shapes: OpenAI/httpx on
    ``.status_code``, requests on ``.status``, google-genai/gRPC on ``.code`` (where
    ``.status`` is the string name). First integer wins; a new backbone slots in by
    error shape here, never a per-provider branch elsewhere."""
    for attr in ("status_code", "status", "code"):
        value = getattr(err, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_default_retryable(err: BaseException) -> bool:
    """Conservative classifier: retry only well-known transient signals."""
    if isinstance(err, RetryableError):
        return True
    if isinstance(err, asyncio.TimeoutError | TimeoutError | ConnectionError):
        return True
    status = status_of(err)
    return status is not None and status in RETRYABLE_STATUS


def is_transient_http(err: BaseException) -> bool:
    """Transient for an httpx call: a transport failure (timeout, connection reset) or a
    retryable status."""
    import httpx

    if isinstance(err, httpx.TransportError):
        return True
    return isinstance(err, httpx.HTTPStatusError) and status_of(err.response) in RETRYABLE_STATUS


def backoff_delay(
    attempt: int,
    *,
    base_delay_s: float,
    max_delay_s: float,
    jitter: bool = True,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with proportional jitter, clamped to ``max_delay_s`` (the jitter
    is re-clamped, so the delay never exceeds the ceiling). Deterministic when
    ``jitter=False``. The one backoff primitive: the retry loop here and the recovery
    policy both compute their sleeps through it."""
    delay = min(base_delay_s * (2**attempt), max_delay_s)
    if jitter:
        delay = min(delay + (rng or random).uniform(0, delay * 0.25), max_delay_s)
    return delay


async def with_retry[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 10.0,
    jitter: bool = True,
    retryable: Callable[[BaseException], bool] = is_default_retryable,
) -> T:
    """Run an async coroutine with exponential backoff on retryable errors.

    ``coro_factory`` returns a fresh coroutine each call (coroutines are
    single-await). Re-raises on non-retryable errors or once attempts are spent.
    """
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except BaseException as err:
            last_error = err
            if not retryable(err) or attempt == max_attempts - 1:
                raise
            await asyncio.sleep(
                backoff_delay(
                    attempt, base_delay_s=base_delay_s, max_delay_s=max_delay_s, jitter=jitter
                )
            )
    raise last_error  # type: ignore[misc]  # unreachable
