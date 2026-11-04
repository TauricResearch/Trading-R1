# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Research routes: ``POST /research`` runs one turn, as a JSON body or, with ``stream=true``, a
Server-Sent Events stream (a leading ``run_start``, live progress, one terminal ``done``, with a
heartbeat comment frame through silent gaps); ``POST /answer`` replies to a suspended
``ask_user``. A turn with a ``session_id`` runs on that session's context and is recorded in its
log, one turn at a time per session; a sessionless run records nothing. ``run_research`` is
looked up here at call time so tests can substitute a fake. Interactive runs have a cap of their
own, apart from the scheduler's: past it a request is answered 429.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from trading_r1.providers import ModelProfile
from trading_r1.providers.health import ModelServerUnreachableError
from trading_r1.research import run_research  # a test seam
from trading_r1.runtime import elicitation
from trading_r1.runtime.modes import resolve_mode
from trading_r1.runtime.session.store import SESSION_ID_PATTERN
from trading_r1.service import preferences, state
from trading_r1.service.events import SSE_VERSION, sse_frame
from trading_r1.service.turns import session_turn
from trading_r1.state.runtime_config import runtime_config

_LOG = logging.getLogger(__name__)
router = APIRouter()

_SENTINEL = object()

# SSE keepalive: tools (visit/search) and non-streaming model calls can run for
# tens of seconds with no events; without a periodic byte the client's stream can
# idle-timeout. A comment frame (ignored by parsers) keeps the connection alive.
_HEARTBEAT_S = 10.0
_PING_FRAME = ": ping\n\n"

_BUSY = "Too many research runs at once; wait for one to finish."


class _RunSlots:
    """The interactive runs in flight, sync and streamed together, counted against a limit."""

    def __init__(self) -> None:
        self._held = 0

    def take(self) -> Callable[[], None]:
        """Claim a slot and return the function that frees it (once, however often called);
        a 429 when none is free."""
        if self._held >= runtime_config().service.max_research_runs:
            raise HTTPException(status_code=429, detail=_BUSY)
        self._held += 1
        freed = False

        def release() -> None:
            nonlocal freed
            if not freed:
                freed = True
                self._held -= 1

        return release


_slots = _RunSlots()


async def _holding(frames: AsyncIterator[str], release: Callable[[], None]) -> AsyncIterator[str]:
    """The frames of a stream, freeing the run's slot when the stream ends or is closed."""
    try:
        async with aclosing(frames):
            async for frame in frames:
                yield frame
    finally:
        release()


async def _drain(queue: asyncio.Queue) -> AsyncIterator[str]:
    """Yield SSE frames from the queue, emitting a heartbeat during silent gaps,
    until the sentinel arrives."""
    while True:
        try:
            kind, data = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
        except TimeoutError:
            yield _PING_FRAME
            continue
        if kind is _SENTINEL:
            break
        yield sse_frame(kind, data)


_EventSink = Callable[[str, dict[str, Any]], None]


async def _sse_bridge(
    runner: Callable[[_EventSink], Awaitable[dict[str, Any]]],
    *,
    terminal: str,
    on_error: dict[str, Any],
    prelude: tuple[str, dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Bridge an ``on_event``-driven run into an SSE frame stream.

    The run executes as a task; its (sync) ``on_event`` pushes events onto a queue this
    generator drains. The run's own ``terminal``-kind event is dropped: the protocol's
    terminal frame carries the authoritative payload: ``runner``'s return value on success,
    ``on_error`` if it raised (preceded by an ``error`` frame). An optional ``prelude`` frame
    is emitted first. On client disconnect mid-drain the task is cancelled in the ``finally``.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(kind: str, data: dict[str, Any]) -> None:
        if kind == terminal:
            return  # superseded by the protocol's terminal frame (below)
        queue.put_nowait((kind, data))

    async def _task() -> None:
        try:
            queue.put_nowait((terminal, await runner(on_event)))
        except Exception as exc:  # noqa: BLE001 (surface as an SSE error + terminal frame)
            queue.put_nowait(("error", {"error": f"{type(exc).__name__}: {exc}"}))
            queue.put_nowait((terminal, on_error))
        finally:
            queue.put_nowait((_SENTINEL, None))

    task = asyncio.create_task(_task())
    if prelude is not None:
        yield sse_frame(*prelude)
    try:
        async for frame in _drain(queue):
            yield frame
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class ResearchRequest(BaseModel):
    """Request body for one research turn, sync or streamed."""

    question: str = Field(..., min_length=1, description="The research question.")
    mode: str | None = Field(
        None, description="Execution mode: auto | research | analytical (unset: auto)."
    )
    profile: str | None = Field(
        None,
        description="The backbone to run (e.g. tauric/gpt/gemini/vllm/ollama). Omit to use the "
        "auto-default; an unknown name is a 400.",
    )
    stream: bool = Field(False, description="Stream progress as Server-Sent Events.")
    session_id: str | None = Field(
        None,
        pattern=f"^{SESSION_ID_PATTERN}$",
        description="Thread this turn into a multi-turn session (omit for a sessionless run).",
    )


class AnswerRequest(BaseModel):
    """Reply to a mid-run `ask_user` question: resolves the suspended tool call."""

    request_id: str = Field(..., description="The ask_user request id from the SSE event.")
    answers: list[dict[str, Any]] = Field(
        ..., description="Answers [{id, question, answer}] the user chose."
    )


async def _run_turn(
    req: ResearchRequest,
    profile: ModelProfile,
    on_event: _EventSink | None = None,
    **run_kw: Any,
) -> dict[str, Any]:
    """Run one research turn. A session turn runs on the session's context and evidence and is
    recorded in its log as it goes: every event, then how it ended, also when it fails or is
    cancelled. A sessionless run records nothing."""
    if not req.session_id:
        return await run_research(
            req.question,
            profile=profile,
            mode=req.mode,
            on_event=on_event,
            config=preferences.run_config(),
            **run_kw,
        )
    return await session_turn(
        req.session_id,
        req.question,
        profile=profile,
        mode=req.mode,
        run=run_research,
        on_event=on_event,
        **run_kw,
    )


def _event_stream(req: ResearchRequest, profile: ModelProfile) -> AsyncIterator[str]:
    """Stream a research turn: a leading `run_start`, live progress, then a terminal `done`
    carrying the full trajectory + session_id."""

    async def runner(on_event: _EventSink) -> dict[str, Any]:
        traj = await _run_turn(
            req,
            profile,
            on_event,
            stream_tokens=True,
            # Streaming = an interactive client that can reply to `ask_user` via /answer.
            elicit_timeout_s=runtime_config().service.ask_user_timeout_s,
        )
        return {"trajectory": traj, "session_id": req.session_id}

    return _sse_bridge(
        runner,
        terminal="done",
        on_error={"trajectory": None, "session_id": req.session_id},
        prelude=(
            "run_start",
            {
                "version": SSE_VERSION,
                "question": req.question,
                "mode": resolve_mode(req.mode),
            },
        ),
    )


@router.post("/answer")
async def answer(req: AnswerRequest) -> dict[str, Any]:
    """Reply to a mid-run `ask_user` question. Resolves the suspended tool call so the
    streaming run resumes with the user's answers. Idempotent: a late/unknown reply
    (the run already timed out or ended) is a no-op, not an error."""
    resolved = elicitation.resolve(req.request_id, req.answers)
    return {"resolved": resolved}


@router.post("/research")
async def research(req: ResearchRequest) -> Any:
    """Run one research trajectory. Sync JSON by default; SSE when stream=true.
    Pass ``session_id`` to thread a session."""
    profile = state.resolve_profile(req.profile)
    release = _slots.take()
    if req.stream:  # freed when the stream ends, or, if it never starts, with the response
        return StreamingResponse(
            _holding(_event_stream(req, profile), release),
            media_type="text/event-stream",
            background=BackgroundTask(release),
        )
    # Non-streaming = no interactive client, so ask_user auto-resolves (elicit_timeout_s=0).
    try:
        traj = await _run_turn(req, profile)
    except ModelServerUnreachableError as e:  # a dead local backend: say so, don't 500
        raise HTTPException(status_code=503, detail=str(e)) from e
    finally:
        release()
    if req.session_id:
        traj = {**traj, "session_id": req.session_id}
    return traj
