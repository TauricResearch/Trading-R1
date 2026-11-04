# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Tool-side progress events: a context-scoped channel so a running tool can
surface live sub-status (e.g. ``visit`` fetching/reading each URL) without
threading an emitter through every handler signature.

The dispatcher opens a :func:`tool_event_scope` around the handler; inside the
handler, :func:`emit_tool_progress` posts a ``tool_progress`` event for the
current call; :func:`emit_tool_event` posts any other tool-raised event (a `source`, a `plan`),
tagged with the call. Both are no-ops when no scope is active (sync tests, non-streaming runs).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

from trading_r1.contract import events

ToolEmit = Callable[[str, dict[str, Any]], None]

_ctx: ContextVar[tuple[ToolEmit, str, str | None] | None] = ContextVar(
    "tr1_tool_emit", default=None
)


@contextlib.contextmanager
def tool_event_scope(emit: ToolEmit | None, name: str, call_id: str | None) -> Iterator[None]:
    """Bind the active tool's emitter for the duration of a handler call."""
    if emit is None:
        yield
        return
    token = _ctx.set((emit, name, call_id))
    try:
        yield
    finally:
        _ctx.reset(token)


def emit_tool_progress(detail: str) -> None:
    """From inside a tool handler: post a live sub-status for the current call."""
    emit_tool_event(events.ToolProgress(detail=detail))


def emit_tool_event(event: events.ToolEvent) -> None:
    """From inside a tool handler: post an event (e.g. ``visit`` emitting a ``source`` per page
    read) tagged with the current tool name + call id. No-op when no scope is active."""
    ctx = _ctx.get()
    if ctx is None:
        return
    emit, name, call_id = ctx
    events.send(emit, event.model_copy(update={"name": name, "call_id": call_id}))
