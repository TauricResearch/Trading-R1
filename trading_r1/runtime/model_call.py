# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Every model call, under one recovery policy.

The backbone's turns (the research rounds, the wrap-up, the grounding review) and the
structured completions (the page reader, `/compact`) fail the same ways, so one
:class:`Budget` decides what an error means: back off and retry a transient one, re-roll a
malformed generation, compact the prompt for an overflow, give up on the rest, each against the
limits in the runtime config. The SDK's own retries are off (`client_for`), so this is the only
policy. :meth:`ModelCaller.turn` adds what only a turn needs (the proactive context check, the
wrap-up deadline, truncation escalation, re-asking a blank turn); :func:`completion` is the same
loop over a structured completion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from trading_r1.providers import ModelProfile, provider_for, usage
from trading_r1.providers.base import AssistantTurn, Completion, Provider
from trading_r1.runtime import recovery
from trading_r1.runtime.context_budget import (
    compact_at,
    estimate_tokens,
    over_budget,
    window_of,
)
from trading_r1.runtime.retry import backoff_delay
from trading_r1.runtime.session import recorder
from trading_r1.state.runtime_config import RuntimeConfig

_LOG = logging.getLogger(__name__)

CONTEXT_FULL = (
    "The model's context window is full, so the run stopped before answering. If earlier turns "
    "fill it, compact them (/compact) or start a new session; if this question's research does, "
    "narrow the question. Runs can also compact on their own: recovery.auto_compact."
)


class ContextFull(Exception):
    """Signal, not a failure: the context is full; compacting it is the user's call."""


class WrapUpDue(Exception):
    """Signal, not a failure: wrap-up time has come; write the answer now."""


class Purpose(StrEnum):
    """What a turn is for, which decides how hard recovery works and what may stop it."""

    # A research round: the prompt is checked against the window before the call (a full
    # context stops the run), each attempt is capped, and the wrap-up time bounds it.
    RESEARCH = "research"
    # Writing the answer: it must be attempted, so the window check only compacts (the endpoint
    # is the judge of a prompt near the limit) and only the run's hard timeout bounds it.
    FINAL = "final"
    # A pass that only polishes an answer already written: one attempt, and any failure
    # keeps the draft.
    OPTIONAL = "optional"


@dataclass(frozen=True)
class Step:
    """What to do after a failed attempt."""

    action: str  # "retry", "compact" or "stop"
    delay_s: float = 0.0


class Budget:
    """One call's retries, spent against the configured limits."""

    def __init__(self, config: RuntimeConfig, provider: Provider) -> None:
        self.cfg = config
        self.provider = provider
        self.transient = self.regenerated = self.escalations = 0

    def after_error(self, err: BaseException) -> Step:
        cfg = self.cfg
        match recovery.classify_model_error(err, self.provider):
            case recovery.BACKOFF if self.transient < cfg.limits.max_transient_retries:
                delay = backoff_delay(
                    self.transient,
                    base_delay_s=cfg.backoff.base_delay_s,
                    max_delay_s=cfg.backoff.max_delay_s,
                    jitter=cfg.backoff.jitter,
                )
                self.transient += 1
                return Step("retry", delay)
            case recovery.REGENERATE if self.regenerated < cfg.limits.max_regenerate_retries:
                self.regenerated += 1
                return Step("retry")
            case recovery.COMPACT:
                return Step("compact")
        return Step("stop")

    def after_blank(self) -> bool:
        """Whether to re-ask a turn with neither tool calls nor content (a failed generation,
        not an answer), under the same bound as a malformed generation."""
        if self.regenerated >= self.cfg.limits.max_regenerate_retries:
            return False
        self.regenerated += 1
        return True

    def escalate(self, max_tokens: int, ceiling: int) -> int | None:
        """The next output budget after a truncation, or None at the ceiling or the cap on
        escalations (accept the best-effort partial)."""
        nxt = recovery.escalate_max_tokens(
            max_tokens, ceiling, self.cfg.generation.max_tokens_escalation_factor
        )
        if nxt is None or self.escalations >= self.cfg.limits.max_truncation_escalations:
            return None
        self.escalations += 1
        return nxt


@dataclass(frozen=True)
class Trimmed:
    """A compaction a call made to fit its prompt: old tool results cut to pointers."""

    tokens_before: int
    tokens_after: int


class ModelCaller:
    """Calls a backbone under the recovery policy."""

    def __init__(self, provider: Provider, profile: ModelProfile, config: RuntimeConfig) -> None:
        self.provider = provider
        self.profile = profile
        self.cfg = config
        self.last_failure = ""  # why the last call gave up, without any secret

    @property
    def window(self) -> int:
        """The model's context window: its own when the profile states it, else the default."""
        return window_of(self.profile, self.cfg.recovery)

    def compact_at(self) -> int | None:
        """The prompt size at which a run compacts itself, or None when auto-compaction is off."""
        return compact_at(self.profile, self.cfg.recovery)

    def max_tokens(self, budget: int | None = None) -> int:
        """`budget` (the configured generation budget by default), within the endpoint's limit."""
        return output_budget(self.profile, self.cfg, budget)

    async def _attempt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        max_tokens: int,
        on_delta: Callable[[str, str], None] | None,
        cap: float | None,
    ) -> AssistantTurn:
        """One call to the provider, written to the session record (when one is bound) with its
        response: whatever the outcome: an answer, an error, a timeout, a cancellation."""
        rec = recorder.current()
        call = (
            rec.model_call(
                self.profile,
                messages,
                tools=tools,
                settings=_settings(self.profile, max_tokens, stream=on_delta is not None),
            )
            if rec
            else None
        )
        with usage.captured() as used:
            try:
                response = await asyncio.wait_for(
                    self.provider.generate(
                        messages, tools=tools or None, max_tokens=max_tokens, on_delta=on_delta
                    ),
                    cap,
                )
            except TimeoutError as err:
                if call:
                    call.end("incomplete", error=err, usage=used)
                raise
            except Exception as err:
                if call:
                    call.end("failed", error=err, usage=used)
                raise
            else:
                if call:
                    call.end(
                        "incomplete" if response.finish_reason == "length" else "completed",
                        output=recorder.turn_output(response),
                        finish_reason=response.finish_reason,
                        usage=used,
                    )
            finally:
                if call:
                    call.end("cancelled", usage=used)  # no effect once ended above
        return response

    async def turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None,
        *,
        purpose: Purpose = Purpose.RESEARCH,
        on_delta: Callable[[str, str], None] | None = None,
        deadline: float | None = None,
        keep_recent: int | None = None,
        on_trim: Callable[[Trimmed], None] | None = None,
    ) -> AssistantTurn | None:
        """One backbone turn under the recovery policy: backoff / regenerate / compact / stop on
        error, `max_tokens` escalation on truncation, a bounded re-ask of a blank turn, and, for
        research, the window check before the call. `deadline` (the agent's wrap-up time, on the
        loop clock) bounds a research attempt: one that reaches it raises :class:`WrapUpDue`, so
        research never eats the time kept for writing the answer. `keep_recent` is how many
        recent tool results compaction leaves whole; `on_trim` hears of each compaction. Returns a
        turn to act on, or None when recovery is exhausted."""
        loop = asyncio.get_running_loop()
        cfg = self.cfg
        research = purpose is Purpose.RESEARCH
        keep = (
            cfg.recovery.compaction_keep_recent_tool_results if keep_recent is None else keep_recent
        )
        max_tokens = self.max_tokens()
        work = messages
        budget = Budget(cfg, self.provider)

        def compacted() -> list[dict[str, Any]] | None:
            """`work` with its old tool results cut, if auto-compaction is on and any can be."""
            if not cfg.recovery.auto_compact:
                raise ContextFull
            cut = recovery.compact_messages(work, keep_recent=keep)
            if cut is None:
                return None
            if on_trim is not None:
                on_trim(Trimmed(estimate_tokens(work), estimate_tokens(cut[0])))
            return cut[0]

        while True:
            # compact at a fraction of the window; with auto-compaction off, only where the
            # prompt no longer leaves room for the answer
            fraction = cfg.recovery.compaction_threshold if cfg.recovery.auto_compact else 1.0
            if over_budget(
                work, window=self.window, max_tokens=max_tokens, fraction=fraction, tools=tools
            ) and (research or cfg.recovery.auto_compact):
                work = compacted() or work

            cap: float | None = float(cfg.limits.per_call_timeout_s) if research else None
            bounded = False  # the wrap-up time, not the per-call cap, bounds this attempt
            if research and deadline is not None:
                left = deadline - loop.time()
                if left <= 0:
                    raise WrapUpDue
                bounded = left <= cap
                cap = min(cap, left)
            try:
                response = await self._attempt(work, tools, max_tokens, on_delta, cap)
            except Exception as err:  # noqa: BLE001 (classified, then acted on)
                if isinstance(err, TimeoutError):
                    if bounded:
                        raise WrapUpDue from None
                    _LOG.warning("model call to %s timed out; retrying", self.profile.name)
                if purpose is Purpose.OPTIONAL:
                    return None
                step = budget.after_error(err)
                if step.action == "stop":
                    self.last_failure = recorder.failure_text(err)
                    return None
                if step.action == "compact":
                    try:
                        cut = compacted()
                    except ContextFull as full:
                        raise full from err
                    if cut is None:
                        self.last_failure = "the conversation no longer fits the model"
                        return None
                    work = cut
                    continue
                delay = step.delay_s
                if research and deadline is not None:
                    delay = min(delay, max(deadline - loop.time(), 0))
                await asyncio.sleep(delay)
                continue

            if response.finish_reason == "length" and purpose is not Purpose.OPTIONAL:
                nxt = budget.escalate(
                    max_tokens, self.max_tokens(cfg.generation.max_tokens_ceiling)
                )
                if nxt is not None:
                    max_tokens = nxt
                    continue
            if not response.is_actionable:
                if purpose is not Purpose.OPTIONAL and budget.after_blank():
                    continue
                self.last_failure = "the model returned an empty reply"
                return None
            return response


def _settings(profile: ModelProfile, max_tokens: int, **extra: Any) -> dict[str, Any]:
    """The generation settings of a request, as recorded."""
    gen = profile.generation
    return {
        "max_tokens": max_tokens,
        "temperature": gen.temperature,
        "top_p": gen.top_p,
        "presence_penalty": gen.presence_penalty,
        **extra,
    }


def output_budget(profile: ModelProfile, config: RuntimeConfig, tokens: int | None = None) -> int:
    """The output budget of a call: `tokens` (the configured generation budget by default),
    within the endpoint's limit. The one place a call's `max_tokens` is decided."""
    return profile.capped(tokens if tokens is not None else config.generation.max_tokens)


async def completion(
    config: RuntimeConfig,
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    client: Any | None = None,
    max_tokens: int | None = None,
    response_schema: dict[str, Any] | None = None,
) -> Completion:
    """A structured completion (`Provider.complete`) under the same policy, for the calls that
    are not the agent's turns: the page reader and `/compact`. The profile's own
    timeout bounds an attempt; `max_tokens` is the configured generation budget by default.
    Raises the last error when recovery is exhausted; the caller
    decides what a failure means."""
    provider = provider_for(profile, client)
    budget = Budget(config, provider)
    max_tokens = output_budget(profile, config, max_tokens)
    while True:
        try:
            return await _recorded_completion(
                provider, profile, messages, max_tokens, response_schema
            )
        except Exception as err:  # noqa: BLE001 (classified, then acted on)
            step = budget.after_error(err)
            if step.action != "retry":
                raise
            await asyncio.sleep(step.delay_s)


async def _recorded_completion(
    provider: Provider,
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    max_tokens: int,
    response_schema: dict[str, Any] | None,
) -> Completion:
    """One structured completion, written to the session record (when one is bound) with its
    response, whatever the outcome."""
    rec = recorder.current()
    settings = _settings(profile, max_tokens)
    if response_schema is not None:
        settings["response_schema"] = response_schema
    call = rec.model_call(profile, messages, settings=settings) if rec else None
    with usage.captured() as used:
        try:
            done = await provider.complete(
                messages, max_tokens=max_tokens, response_schema=response_schema
            )
        except Exception as err:
            if call:
                call.end("failed", error=err, usage=used)
            raise
        else:
            if call:
                call.end(
                    "incomplete" if done.finish_reason == "length" else "completed",
                    output=[{"type": "text", "text": done.text}],
                    finish_reason=done.finish_reason,
                    usage=used,
                )
        finally:
            if call:
                call.end("cancelled", usage=used)  # no effect once ended above
    return done
