# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The typed state of one run: how it ended, what it has written, where it stands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from trading_r1.runtime.memory import MemoryStore
from trading_r1.runtime.skill_registry import SkillSessionState
from trading_r1.runtime.tool_registry import ToolSessionState

EventCallback = Callable[[str, dict[str, Any]], None]


class Termination(StrEnum):
    """How a run ended. The values are the strings sessions, the service and the TUI carry."""

    COMPLETED = "answer"  # the string is what session logs hold
    BUDGET_REPORT = "budget_report"  # written at the budget (the wrap-up)
    TIMEOUT = "timeout"
    MAX_ROUNDS = "max_rounds"
    CONTEXT_FULL = "context_full"
    TRUNCATED = "length"  # the answer was cut by the output limit
    ERROR = "error"
    CANCELLED = "cancelled"  # the user stopped the turn

    @classmethod
    def of_finish(cls, finish_reason: str | None) -> Termination:
        """How a run ends when the model's last message carried this finish reason."""
        if finish_reason in ("stop", "tool_calls"):
            return cls.COMPLETED
        return cls.TRUNCATED if finish_reason == "length" else cls.ERROR

    @property
    def completed(self) -> bool:
        """Whether the turn finished, at its budget or before it."""
        return self in (Termination.COMPLETED, Termination.BUDGET_REPORT)

    @property
    def cut_off(self) -> bool:
        """Whether the turn was stopped by a limit, with whatever it had written."""
        return self in (Termination.TRUNCATED, Termination.TIMEOUT, Termination.MAX_ROUNDS)


# What the harness says in a conversation. It goes as a user message, since providers have no other
# role for it, and the label keeps it from being taken for the user's words.
_HARNESS_LABEL = "[Harness] "


def harness_note(text: str) -> dict[str, Any]:
    return {"role": "user", "content": _HARNESS_LABEL + text}


def is_harness_note(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, str)
        and content.startswith(_HARNESS_LABEL)
    )


def last_text(messages: list[dict[str, Any]]) -> str:
    """What the model last said in these messages with nothing left to call: what a consumer
    with something to send takes by default. Empty when it said nothing of the kind."""
    for m in reversed(messages):
        content = m.get("content")
        spoke = m.get("role") == "assistant" and isinstance(content, str) and content.strip()
        if spoke and not m.get("tool_calls"):
            return content.strip()
    return ""


class RunPolicy(StrEnum):
    """Who is behind a run. In an unattended run (a chat message from the paired owner) no one
    can answer while it works, so it cannot ask mid-run; the user can reply afterwards. A
    scheduled run has nobody there at all, so it cannot change the user's notes either."""

    INTERACTIVE = "interactive"
    UNATTENDED = "unattended"
    SCHEDULED = "scheduled"


@dataclass
class RunContext:
    """What a run binds into its tool registry: the run's own state, not the catalog's."""

    session_state: ToolSessionState
    skill_state: SkillSessionState
    emit: EventCallback
    elicit_timeout_s: float = 0.0
    memory: MemoryStore | None = None
    policy: RunPolicy = RunPolicy.INTERACTIVE


@dataclass
class RunState:
    """Everything a run's stages share, updated in place so a mid-run cancellation (the run's
    timeout) still leaves the caller an accurate round count and any drafted answer."""

    messages: list[dict[str, Any]]
    wrap_up_at: float  # loop-clock time at which research stops and the answer is written
    evidence_from: int  # this run's evidence records are the ones after these
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    turn_from: int = 0  # this turn's messages are the ones from this index
    termination: Termination = Termination.ERROR
    error: str = ""  # why a run that failed failed, safe to show
    # text written since the research ended: the answer is the model's last message and the
    grounding_reviewed: bool = False
    verified: bool = False  # the verification stage ran and its report replaced the draft
