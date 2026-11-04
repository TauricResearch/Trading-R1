# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Human-in-the-loop elicitation: suspend-and-resume for the ``ask_user`` tool.

The model calls a tool to ask the human a question; the tool call **blocks** on a future while
the client renders the questions and sends back answers out-of-band; the answers return **as the
tool result** and the ReAct loop resumes in the same turn, so the model keeps its full context
and its tool-call/result pairing stays intact.

The seam: an :class:`Elicitor` (built per run from the run's event emitter) posts an
``ask_user`` SSE event and awaits a future in the run's event loop, keyed by ``request_id``
in the ``_PENDING`` registry; the service's reply endpoint calls :func:`resolve` to complete
it in the same process, and the agent resumes with the answer. A non-interactive run
(headless ``-p``, non-streaming) has ``timeout_s <= 0`` and resolves to the recommended
(first) option immediately; an unanswered interactive question times out to the same, so a
run always makes progress.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from trading_r1.contract import events

EventEmit = Callable[[str, dict[str, Any]], None]

# request_id -> the future the ask_user handler is awaiting. Process-wide so the reply
# endpoint (a separate request) can resolve the in-flight run's question.
_PENDING: dict[str, asyncio.Future] = {}


def _recommended(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The proceed-on-best-judgment fallback: each question's first option (its
    recommended answer), used on timeout / when non-interactive."""
    out: list[dict[str, Any]] = []
    for q in questions:
        opts = q.get("options") or []
        first = opts[0].get("label", "") if opts and isinstance(opts[0], dict) else ""
        out.append({"id": q.get("id", ""), "question": q.get("question", ""), "answer": first})
    return out


@dataclass
class Elicitor:
    """Bound to one run: emits ``ask_user`` and awaits the reply (or auto-resolves)."""

    emit: EventEmit
    timeout_s: float

    async def ask(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Non-interactive run: no client to answer, so proceed on the recommended answers
        # rather than blocking. (Same effect as a timeout, taken immediately.)
        if self.timeout_s <= 0 or not questions:
            return _recommended(questions)

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        _PENDING[request_id] = fut
        try:
            # emit inside the try so a failing emit still hits the finally (no leaked future).
            events.send(
                self.emit,
                events.AskUser(
                    request_id=request_id,
                    questions=[events.AskQuestion.model_validate(q) for q in questions],
                    timeout_s=self.timeout_s,
                ),
            )
            return await asyncio.wait_for(fut, self.timeout_s)
        except TimeoutError:
            return _recommended(questions)  # nobody answered in time: proceed on best judgment
        # CancelledError (client disconnect / run torn down) is NOT caught: it must propagate
        # so the whole run unwinds instead of the detached agent continuing past the question.
        finally:
            _PENDING.pop(request_id, None)


def resolve(request_id: str, answers: list[dict[str, Any]]) -> bool:
    """Complete a pending ``ask_user`` with the user's answers. Returns False if the
    request is unknown or already settled (a late/duplicate reply is ignored, not an error)."""
    fut = _PENDING.get(request_id)
    if fut is None or fut.done():
        return False
    fut.set_result(answers)
    return True
