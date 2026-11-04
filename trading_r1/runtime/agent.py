# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The agent: a model profile, a tool registry and the settings a run needs.

`Agent.run` sets a run up (its evidence, its per-run tools, its system prompt, its state) and
drives the stages in `loop.Run` under one hard timeout. Every exit funnels through
:meth:`Agent._build_result` into one trajectory dict.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

from trading_r1.contract import events
from trading_r1.providers import ModelProfile, provider_for
from trading_r1.providers.base import tool_arguments
from trading_r1.providers.usage import Usage, metered
from trading_r1.runtime.evidence import EvidenceStore
from trading_r1.runtime.loop import Run
from trading_r1.runtime.memory import MemoryStore
from trading_r1.runtime.model_call import ModelCaller
from trading_r1.runtime.modes import MODES, ModeSpec
from trading_r1.runtime.prompts import build_system_prompt
from trading_r1.runtime.run_state import (
    EventCallback,
    RunContext,
    RunPolicy,
    RunState,
    Termination,
    last_text,
)
from trading_r1.runtime.session import recorder
from trading_r1.runtime.skill_registry import SkillRegistry, SkillSessionState
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState
from trading_r1.state.runtime_config import RuntimeConfig, load_runtime_config

# Binds a run's own state into its registry: the catalog plus the per-run tools (evidence
# readback, the meta-tools, ask_user, memory). Supplied by the wiring, so the runtime does not
# import the tools.
Bind = Callable[[ToolRegistry, RunContext], ToolRegistry]


def wrap_up_offset(limits: Any) -> float:
    """Seconds from the start of a run to its wrap-up: the timeout less the wrap-up reserve,
    which is at most half the budget (a short budget still gets research time)."""
    return limits.run_timeout_s - min(limits.wrap_up_reserve_s, limits.run_timeout_s / 2)


def _listed(args: dict[str, Any], key: str) -> list[str]:
    """The names a call's argument lists; a call the dispatcher refused may hold anything."""
    value = args.get(key)
    return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


def loaded_earlier(
    history: list[dict[str, Any]] | None, skills: SkillRegistry | None = None
) -> list[str]:
    """The tools the calls in ``history`` loaded or used: what ``load_tool`` named, what a loaded
    skill needs, and each tool called. Their schemas are not in the history, so a later turn
    offers them again. What compaction summarized away is loaded again when wanted."""
    names: list[str] = []
    for message in history or []:
        for call in message.get("tool_calls") or []:
            name, args = call["function"]["name"], tool_arguments(call["function"]["arguments"])
            if name == "load_tool":
                names += _listed(args, "tools")
            elif name == "load_skill" and skills is not None:
                for skill in _listed(args, "skills"):
                    if (found := skills.get(skill)) is not None:
                        names += found.requires_tools
            else:
                names.append(name)
    return names


class Agent:
    """A finance deep-research orchestrator over a model profile + tool registry."""

    def __init__(
        self,
        *,
        profile: ModelProfile,
        registry: ToolRegistry,
        config: RuntimeConfig | None = None,
        client: Any | None = None,
        bind: Bind | None = None,
        finalize: Callable[[str], tuple[str, dict[str, Any]]] | None = None,
        skill_registry: SkillRegistry | None = None,
        stream_tokens: bool = False,
        grounding_review: bool = False,
        mode: ModeSpec | None = None,
        elicit_timeout_s: float = 0.0,
        memory: MemoryStore | None = None,
        evidence: EvidenceStore | None = None,
        policy: RunPolicy = RunPolicy.INTERACTIVE,
        delivery: str = "",
    ) -> None:
        self.profile = profile
        self.registry = registry
        self.config = config or load_runtime_config()
        self.client = client
        self.provider = provider_for(profile, client)
        self.caller = ModelCaller(self.provider, profile, self.config)
        self._bind = bind
        self.skill_registry = skill_registry
        # When True, model calls stream and their content deltas are surfaced as ``token``
        # events (the report fills live). Off by default, so a non-streaming request issues one
        # non-streaming call.
        self.stream_tokens = stream_tokens
        # Optional post-processor applied to the turn's last text at the single exit (research
        # wiring passes citation post-processing). Keeps the loop itself citation-agnostic.
        self._finalize = finalize
        # When True, after the draft the backbone makes one pass over its own
        # report, re-reading it against the sources already in context and revising any
        # specific claim it can't ground.
        self.grounding_review = grounding_review
        # The execution mode: a declarative profile (guidance prose + which domain packs the
        # catalog foregrounds). Defaults to auto (full toolkit).
        self._mode = mode or MODES["auto"]
        # How long the `ask_user` tool blocks for an answer before auto-resolving to the
        # recommended option. >0 only for an interactive run (a streaming client that can
        # reply); 0 (default) = non-interactive: questions resolve to defaults immediately.
        self._elicit_timeout_s = elicit_timeout_s
        # The user's notes, on a session turn (None elsewhere, so other runs stay reproducible).
        self._memory = memory
        self._policy = policy
        self._delivery = delivery
        # The evidence store this agent records into: the session's (a run without one gets its
        # own).
        self.evidence = evidence if evidence is not None else EvidenceStore()

    async def run(
        self,
        question: str,
        *,
        on_event: EventCallback | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run the loop to completion and return the trajectory dict.

        ``history`` is prior-turn messages (system already stripped) threaded before the new
        question, for multi-turn sessions."""
        emit = on_event or (lambda _k, _d: None)
        session_state = ToolSessionState(evidence=self.evidence, run=uuid.uuid4().hex[:8])
        if (bound := recorder.current()) is not None:
            bound.run = session_state.run
        # tools an earlier turn loaded stay callable
        session_state.load(loaded_earlier(history, self.skill_registry), self.registry)
        context = RunContext(
            session_state,
            SkillSessionState(),
            emit,
            elicit_timeout_s=self._elicit_timeout_s,
            memory=self._memory,
            policy=self._policy,
        )
        registry = self._bind(self.registry, context) if self._bind else self.registry
        system_prompt = build_system_prompt(
            registry,
            skill_registry=self.skill_registry,
            mode=self._mode,
            memory=(
                self._memory.block(editable=self._policy is not RunPolicy.SCHEDULED)
                if self._memory
                else ""
            ),
            policy=self._policy,
            delivery=self._delivery,
        )
        limits = self.config.limits
        state = RunState(
            messages=[
                {"role": "system", "content": system_prompt},
                *(history or []),
                {"role": "user", "content": question},
            ],
            wrap_up_at=asyncio.get_running_loop().time() + wrap_up_offset(limits),
            evidence_from=len(self.evidence),
            turn_from=1 + len(history or []),
        )
        started = time.monotonic()

        # One hard ceiling over the whole run (model calls, their retries/backoff, tool
        # execution, and the grounding review) via a single structured-timeout scope. On
        # expiry the in-flight await is cancelled; the state (updated in place) still carries
        # an accurate round count and any drafted answer.
        with metered() as usage:
            try:
                async with asyncio.timeout(limits.run_timeout_s):
                    await Run(self, state, registry, session_state, emit).drive()
            except TimeoutError:
                # the optional passes after an answer may run out of time; the answer stands
                if state.termination is not Termination.COMPLETED:
                    state.termination = Termination.TIMEOUT

        # A run that hit a budget (time or steps) having written nothing says why, and what to try
        # (usually a smaller model not converging). It is the outcome, not something it said.
        if state.termination in (
            Termination.TIMEOUT,
            Termination.MAX_ROUNDS,
        ) and not last_text(state.messages[state.turn_from :]):
            budget = "time budget" if state.termination is Termination.TIMEOUT else "step budget"
            state.error = (
                f"Stopped before answering: the run hit its {budget}. This can happen when a "
                "smaller/local backbone doesn't converge on a complex, multi-part question; try a "
                "narrower question or a more capable backbone."
            )

        return self._build_result(
            question, state, elapsed_s=round(time.monotonic() - started, 3), usage=usage, emit=emit
        )

    def _build_result(
        self,
        question: str,
        state: RunState,
        *,
        elapsed_s: float,
        usage: Usage,
        emit: EventCallback,
    ) -> dict[str, Any]:
        """The single exit point: assemble the trajectory, applying the finalize hook
        (citation post-processing) when present."""
        text = last_text(state.messages[state.turn_from :])
        citation_stats: dict[str, Any] = {}
        if self._finalize is not None and text:
            text, citation_stats = self._finalize(text)
        events.send(emit, events.Done(termination=state.termination.value))
        return {
            "question": question,
            "text": text,
            "termination": state.termination.value,
            "error": state.error,
            "rounds": state.rounds,
            "messages": state.messages,
            "tool_log": state.tool_log,
            "citation_stats": citation_stats,
            "grounding_reviewed": state.grounding_reviewed,
            "verified": state.verified,
            "elapsed_s": elapsed_s,
            "model": self.profile.model,
            # tokens per profile over every call the run made (backbone, reader, review)
            "usage": usage,
            # the evidence this run recorded: what a session appends
            "evidence": self.evidence.to_list(since=state.evidence_from),
        }
