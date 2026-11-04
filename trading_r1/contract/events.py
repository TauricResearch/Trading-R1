# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The events a run streams, as models: the one definition of the SSE protocol.

A run reports what it does through events: the loop, the tools and the elicitation each build
one of these models and hand it to :func:`send`; the service validates every frame against its
model before it ships; and the TUI's types (`tui/src/lib/events.gen.ts`) are generated from the
same models.

Frames are ``event: <kind>\\ndata: <json>\\n\\n`` on a versioned ``text/event-stream``:

  run_start     once, first, with the protocol version
  round_start   per ReAct round
  reasoning     the model's thinking, streamed
  token         the report as it is written, streamed
  tool_call     per tool invocation
  tool_progress a running tool's live sub-status
  tool_result   per tool result (``result``: truncated data or compute output for the trace)
  source        a numbered source: a page read, or an article a data tool returned (live rail)
  record        a data result's evidence record (live rail)
  plan          the live research plan (``update_plan``)
  phase         a coarse processing phase (grounding, report) for live status
  usage         the live context meter, per round
  compaction    the context is compacted, by trimming old tool results or by a summary
  ask_user      the model asks the user; the run awaits ``POST /answer``
  answer        the report once written, authoritative; a client reconciles to it
  error         a failed run
  done          once, last: the whole trajectory with the session id (the sync body)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

SSE_VERSION = "0.1"


# Every model here is a wire shape: what is sent is what is typed, defaults included.
_WIRE = {"json_schema_serialization_defaults_required": True}


class Event(BaseModel):
    """One event on the stream. ``kind`` is its name on the wire."""

    model_config = ConfigDict(extra="forbid", **_WIRE)

    kind: ClassVar[str]


class ToolEvent(Event):
    """An event raised from inside a tool handler, tagged with the call it came from."""

    name: str | None = None
    call_id: str | None = None


class RunStart(Event):
    kind = "run_start"
    version: str
    question: str
    mode: str


class RoundStart(Event):
    kind = "round_start"
    round: int


class Reasoning(Event):
    kind = "reasoning"
    text: str


class Token(Event):
    kind = "token"
    text: str


class ToolCall(Event):
    kind = "tool_call"
    name: str
    call_id: str
    args: dict[str, Any]
    round: int


class ToolProgress(ToolEvent):
    kind = "tool_progress"
    detail: str


class ToolResult(Event):
    kind = "tool_result"
    name: str
    call_id: str
    ok: bool
    elapsed_s: float
    round: int
    result: str | None  # truncated data/compute output for the trace; None otherwise


class Source(ToolEvent):
    kind = "source"
    index: int  # the [N] marker
    url: str
    title: str
    headline: str


class DataRecord(Event):
    kind = "record"
    call_id: str
    id: str  # the evidence record's id
    tool: str
    subject: str | None
    view: str | None
    as_of: str | None
    source: str | None


PlanStatus = Literal["pending", "in_progress", "completed"]


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", **_WIRE)

    step: str
    status: PlanStatus


class Plan(ToolEvent):
    kind = "plan"
    items: list[PlanItem]


class Phase(Event):
    kind = "phase"
    label: str


class Usage(Event):
    kind = "usage"
    tokens_used: int
    context_window: int
    used_pct: float
    compact_at: int | None  # the token count at which the context is compacted; None when off


class Compaction(Event):
    """The context is being compacted (``start``, a summary that takes a model call), has been
    (``end``), or could not be (``failed``). ``trim`` cuts old tool results (they stay
    recoverable from the evidence); ``summary`` replaces the history with a summary."""

    kind = "compaction"
    stage: Literal["start", "end", "failed"]
    how: Literal["trim", "summary"]
    tokens_before: int
    tokens_after: int | None = None


class AskOption(BaseModel):
    model_config = ConfigDict(extra="forbid", **_WIRE)

    label: str
    description: str = ""


class AskQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", **_WIRE)

    id: str  # pairs the user's answer back to the question
    question: str
    header: str
    options: list[AskOption]


class AskUser(Event):
    kind = "ask_user"
    request_id: str
    questions: list[AskQuestion]
    timeout_s: float = 0.0  # how long the run waits for a reply; the client shows the same


class LastMessage(Event):
    """The model's last message is now this text: what did not stream (a wrap-up, a rewrite), or
    what a retry's stream left repeated."""

    kind = "last_message"
    content: str


class RunError(Event):
    kind = "error"
    error: str


class Citation(BaseModel):
    """A source the run read, as the trajectory lists it."""

    model_config = ConfigDict(extra="forbid", **_WIRE)

    index: int
    url: str
    title: str
    record: str | None = None  # the evidence record that produced it


class ToolLogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", **_WIRE)

    name: str
    ok: bool


class Trajectory(BaseModel):
    """The run artifact as a client reads it; the full trajectory carries more (messages,
    evidence, usage), which is not part of the protocol."""

    model_config = ConfigDict(extra="ignore", **_WIRE)

    question: str
    text: str = ""  # the turn's last text as shown, with its References; empty if it left none
    termination: str
    error: str | None = None
    rounds: int
    tool_log: list[ToolLogEntry] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    citation_stats: dict[str, Any] = Field(default_factory=dict)
    elapsed_s: float | None = None
    session_id: str | None = None


class Done(Event):
    kind = "done"
    termination: str | None = None  # the loop's own report of how the run ended
    trajectory: Trajectory | None = None  # the stream's terminal frame carries the trajectory
    session_id: str | None = None


EVENTS: dict[str, type[Event]] = {
    cls.kind: cls
    for cls in (
        RunStart,
        RoundStart,
        Reasoning,
        Token,
        ToolCall,
        ToolProgress,
        ToolResult,
        Source,
        DataRecord,
        Plan,
        Phase,
        Usage,
        Compaction,
        AskUser,
        LastMessage,
        RunError,
        Done,
    )
}

Emit = Callable[[str, dict[str, Any]], None]


def send(emit: Emit, event: Event) -> None:
    """Hand `event` to a sink as its wire name and JSON-ready data."""
    emit(event.kind, event.model_dump(mode="json"))


def validate(kind: str, data: dict[str, Any]) -> Event:
    """The event model for a frame, or an error: an unknown kind or a shape the model refuses
    is drift, and fails here rather than reaching a client."""
    if kind not in EVENTS:
        raise ValueError(f"unknown SSE event type: {kind!r} (add its model to contract.events)")
    return EVENTS[kind].model_validate(data)
