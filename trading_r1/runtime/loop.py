# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""One run, as stages over its :class:`RunState`.

The loop is one bounded ReAct cycle: a model step; if the message carries tool calls, a tool step
and round again; otherwise the message is the answer and the run finishes (the optional
grounding review, then the answer). At its round cap or its wrap-up time a run that did research
writes its answer from what it has (`wrap_up`). Every stage reads and updates the shared state,
calls the model through the one recovery policy (`model_call`), and emits its own events.

Native tool calling: the provider's tool-call parser owns the wire format; we never parse
tool-call syntax ourselves.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from trading_r1.contract import events
from trading_r1.providers.base import AssistantTurn, tool_arguments
from trading_r1.runtime.context_budget import estimate_tokens, schema_tokens
from trading_r1.runtime.dispatch import DispatchResult, dispatch_json_args
from trading_r1.runtime.model_call import CONTEXT_FULL, ContextFull, Purpose, Trimmed, WrapUpDue
from trading_r1.runtime.prompts import GROUNDING_REVIEW_PROMPT, VERIFY_PROMPT, WRAP_UP_PROMPT
from trading_r1.runtime.run_state import EventCallback, RunState, Termination, harness_note
from trading_r1.runtime.session import recorder
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState

if TYPE_CHECKING:
    from trading_r1.runtime.agent import Agent

_LOG = logging.getLogger(__name__)

_UI_RESULT_BUDGET = 600  # chars of a data/compute result surfaced to the UI trace
# The argument that names what a data call is about, in the order the tools use them.
_SUBJECT_ARGS = ("symbol", "symbols", "series", "category", "query", "date")


def _ui_tool_result(result: DispatchResult) -> str | None:
    """The grounding data behind a tool call, surfaced to the UI trace so a reader can
    audit the numbers a report rests on. Only data and compute results (the fetched or
    computed figures that are otherwise invisible): web evidence already lives in the
    Sources rail, and controls carry nothing to audit. The tool's own text, truncated;
    ``None`` when there's nothing to show."""
    if not result.ok or result.result_kind not in ("data", "compute"):
        return None
    md = result.body.strip()
    if not md:
        return None
    if len(md) <= _UI_RESULT_BUDGET:
        return md
    tail = _UI_RESULT_BUDGET // 3  # head + tail so end-of-output figures (implied/range) survive
    return md[: _UI_RESULT_BUDGET - tail].rstrip() + "\n…\n" + md[-tail:].lstrip()


def _record_event(name: str, args: dict[str, Any], result: DispatchResult) -> dict | None:
    """The rail's view of a data result's evidence record: its id, the tool, what it is about,
    the date its data runs to and the upstream source; ``None`` for anything that is not a
    recorded data result."""
    if not (result.ok and result.evidence_id and result.result_kind == "data"):
        return None
    meta = result.meta
    # the tool's own reading of the request first (validated, defaults applied), else the args
    subject = meta.get("subject") or next((args[k] for k in _SUBJECT_ARGS if args.get(k)), None)
    if isinstance(subject, list):
        subject = ", ".join(map(str, subject))
    return {
        "id": result.evidence_id,
        "tool": name.removeprefix("data_"),
        "subject": str(subject) if subject is not None else None,
        "view": meta.get("view") or args.get("view") or args.get("statement"),
        "as_of": meta.get("as_of"),
        "source": meta.get("source"),
    }


class Run:
    """The stages of one run over its state."""

    def __init__(
        self,
        agent: Agent,
        state: RunState,
        registry: ToolRegistry,
        session_state: ToolSessionState,
        emit: EventCallback,
    ) -> None:
        self.agent = agent
        self.cfg = agent.config
        self.state = state
        self.registry = registry
        self.session_state = session_state
        self.emit = emit

    def send(self, event: events.Event) -> None:
        events.send(self.emit, event)

    async def drive(self) -> None:
        """The loop, run inside the caller's run-timeout scope. Returns when the model answers,
        recovery is exhausted, or the budget is hit; the run timeout is enforced by the caller's
        scope, not here."""
        state = self.state
        limits = self.cfg.limits
        while True:
            # At its round cap or its wrap-up time, a run that did research writes its answer
            # from what it has rather than ending with none.
            if state.rounds and (
                state.rounds >= limits.max_rounds
                or asyncio.get_running_loop().time() >= state.wrap_up_at
            ):
                await self.wrap_up()
                return
            state.rounds += 1
            self.send(events.RoundStart(round=state.rounds))
            self._emit_usage()
            turn = await self.model_step()
            if turn is None:
                return
            state.messages.append(turn.to_message())
            if turn.tool_calls:
                await self.tool_step(turn)
                continue
            await self.finish(turn)
            return

    def _emit_usage(self) -> None:
        """The live context meter: the message list has grown with the prior round's tool
        results, so report the running token estimate each round: everything a request carries,
        as /status counts it."""
        caller = self.agent.caller
        window = caller.window
        used = estimate_tokens(self.state.messages) + schema_tokens(
            self.session_state.visible_schemas(self.registry)
        )
        self.send(
            events.Usage(
                tokens_used=used,
                context_window=window,
                used_pct=round(100 * used / window, 1) if window else 0.0,
                compact_at=caller.compact_at(),
            )
        )

    def _on_trim(self, trimmed: Trimmed) -> None:
        """Old tool results were cut to fit the prompt: say so, with the tokens it freed."""
        self.send(
            events.Compaction(
                stage="end",
                how="trim",
                tokens_before=trimmed.tokens_before,
                tokens_after=trimmed.tokens_after,
            )
        )

    def _on_delta(self) -> Callable[[str, str], None] | None:
        """The streaming sink: content and reasoning deltas become `token` / `reasoning`
        events when the run streams, so the report fills live."""
        if not self.agent.stream_tokens:
            return None

        def on_delta(kind: str, text: str) -> None:
            if text and kind == "content":
                self.send(events.Token(text=text))
            elif text and kind == "reasoning":
                self.send(events.Reasoning(text=text))

        return on_delta

    async def model_step(self) -> AssistantTurn | None:
        """One model call. None ends the run: the wrap-up wrote its answer, or the termination
        and error are set."""
        state = self.state
        try:
            turn = await self.agent.caller.turn(
                state.messages,
                self.session_state.visible_schemas(self.registry),
                on_delta=self._on_delta(),
                deadline=state.wrap_up_at,
                on_trim=self._on_trim,
            )
        except WrapUpDue:
            await self.wrap_up()
            return None
        except ContextFull:
            state.termination = Termination.CONTEXT_FULL
            state.error = CONTEXT_FULL
            self.send(events.RunError(error=CONTEXT_FULL))
            return None
        except Exception as exc:  # noqa: BLE001 (backstop; recovery normally returns None)
            state.termination = Termination.ERROR
            state.error = recorder.failure_text(exc)
            self.send(events.RunError(error=state.error))
            return None
        if turn is None:  # recovery exhausted (the run timeout arrives as a cancellation)
            state.termination = Termination.ERROR
            failure = self.agent.caller.last_failure
            state.error = failure
            self.send(events.RunError(error=failure or "model call failed after recovery"))
            return None
        return turn

    async def tool_step(self, turn: AssistantTurn) -> None:
        """Dispatch the message's calls and append their results."""
        state = self.state
        for tc in turn.tool_calls:
            result = await self._execute(tc)
            state.messages.append(result.to_message(tc.id))
            state.tool_log.append(result.to_log())

    async def _execute(self, tc: Any) -> DispatchResult:
        """Dispatch one tool call, with its call and result events."""
        round_no = self.state.rounds
        args = tool_arguments(tc.arguments)
        self.send(events.ToolCall(name=tc.name, call_id=tc.id, args=args, round=round_no))
        started = time.monotonic()
        result = await dispatch_json_args(
            tc.name,
            tc.arguments,
            registry=self.registry,
            session_state=self.session_state,
            call_id=tc.id,
            emit=self.emit,
        )
        self.send(
            events.ToolResult(
                name=tc.name,
                call_id=tc.id,
                ok=result.ok,
                elapsed_s=round(time.monotonic() - started, 2),
                round=round_no,
                result=_ui_tool_result(result),
            )
        )
        if (record := _record_event(tc.name, args, result)) is not None:
            self.send(events.DataRecord(call_id=tc.id, **record))
        return result

    async def finish(self, turn: AssistantTurn) -> None:
        """The model wrote without calling: the turn is done, and a research report is then
        grounded and announced."""
        state = self.state
        # `turn` is actionable by construction, so the content of a message with no tool calls
        # is non-blank.
        text = turn.content.strip()
        state.termination = Termination.of_finish(turn.finish_reason)
        # Check only a turn that did research: a no-tool reply (a meta/"what can you do" question,
        # or a pure-knowledge reply) has nothing to check against, and the pass would clobber it.
        if (
            state.termination is Termination.COMPLETED
            and text
            and any(t.get("evidence") for t in state.tool_log)  # it researched, not only planned
        ):
            check = self.verify if self.cfg.verify.enabled else None
            review = self.review_grounding if self.agent.grounding_review else None
            if check and (checked := await check(text)) is not None:
                state.verified = checked != text
                text = checked
            elif not check and review and (reviewed := await review(text)) is not None:
                state.grounding_reviewed = reviewed != text
                text = reviewed
        self.send(events.LastMessage(content=text))

    async def verify(self, draft: str) -> str | None:
        """The report's numbers held to the record: the agent, with its tools, checks each figure
        against the result it came from and recomputes the ones it worked out, then writes the
        report again. Best-effort: when it cannot finish in time, or a call fails,
        the draft stands and the transcript is as it was; a rewrite replaces the draft."""
        state = self.state
        loop = asyncio.get_running_loop()
        if loop.time() >= state.wrap_up_at:
            return None
        self.send(events.Phase(label="verify"))
        before = list(state.messages)
        report: str | None = None
        try:
            state.messages.append({"role": "user", "content": VERIFY_PROMPT})
            for step in range(self.cfg.verify.max_rounds + 1):
                last = (
                    step == self.cfg.verify.max_rounds
                )  # out of rounds: the report, without tools
                if last:
                    state.messages.append({"role": "user", "content": "Write the report now."})
                try:
                    turn = await self.agent.caller.turn(
                        state.messages,
                        None if last else self.session_state.visible_schemas(self.registry),
                        purpose=Purpose.OPTIONAL,
                    )
                except Exception:  # noqa: BLE001 (best-effort; keep the draft)
                    turn = None
                if turn is None or loop.time() >= state.wrap_up_at:
                    break
                state.messages.append(turn.to_message())
                if turn.tool_calls:
                    await self.tool_step(turn)
                    continue
                report = turn.content.strip() or None
                break
        finally:  # a run cut off mid-check too
            # the transcript keeps the report, not the checking: the draft gives way to the rewrite
            state.messages[:] = (
                [*before[:-1], {"role": "assistant", "content": report}] if report else before
            )
        return report

    async def review_grounding(self, draft: str) -> str | None:
        """One backbone pass to ground the draft against the sources already in the
        conversation: the model re-reads its own report and revises any specific claim it can't
        tie to something it actually read, attributing or softening it. Non-streaming
        (the authoritative `answer` event carries the rewrite). Best-effort: any
        failure keeps the draft, and a rewrite replaces the draft in the transcript."""
        self.send(events.Phase(label="grounding"))
        messages = self.state.messages
        review = f"{GROUNDING_REVIEW_PROMPT}\n\n---\n\n{draft}"
        try:
            response = await self.agent.caller.turn(
                [*messages, {"role": "user", "content": review}], None, purpose=Purpose.OPTIONAL
            )
        except Exception:  # noqa: BLE001 (best-effort; keep the draft)
            return None
        new = response.content.strip() if response else ""
        if not new:
            return None
        messages[:] = messages[:-1] + [{"role": "assistant", "content": new}]
        return new

    async def wrap_up(self) -> None:
        """An agent at its budget: a no-tools turn to write its answer from what it has (else
        the run ends at the budget). It sees the context compacted as the research rounds did;
        the run's hard timeout bounds it. Reached in round 1 too, when a single call outlasts
        the research time: the answer is then written from what the model knows, and says so."""
        state = self.state
        at = (
            Termination.MAX_ROUNDS
            if state.rounds >= self.cfg.limits.max_rounds
            else Termination.TIMEOUT
        )
        self.send(events.Phase(label="report"))
        prompt = harness_note(WRAP_UP_PROMPT)
        try:
            response = await self.agent.caller.turn(
                [*state.messages, prompt], None, purpose=Purpose.FINAL, keep_recent=0
            )
        except Exception:  # noqa: BLE001 (best-effort)
            _LOG.warning("wrap-up call failed; the run ends at its %s", at.value, exc_info=True)
            state.termination = at
            return
        report = response.content.strip() if response else ""
        if not report:
            _LOG.warning("wrap-up returned no content; the run ends at its %s", at.value)
            state.termination = at
            return
        state.messages += [prompt, {"role": "assistant", "content": report}]
        state.termination = Termination.BUDGET_REPORT
        self.send(events.LastMessage(content=report))  # replaces any draft streamed before the cut
