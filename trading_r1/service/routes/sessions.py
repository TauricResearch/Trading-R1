# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Session routes: list saved sessions (``GET /sessions``), replay one (``/sessions/{id}``), its
HTML report (``/sessions/{id}/report``), compact its history (``POST /compact``) and its context
usage (``GET /status``). All read the one session store in ``service.state``, off the event loop;
a compaction waits for a running turn of the same session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from trading_r1.providers.discovery import with_context_window
from trading_r1.report import render_html, snapshot_from_session
from trading_r1.runtime.context_budget import compact_at, estimate_tokens, window_of
from trading_r1.runtime.session import recorder
from trading_r1.runtime.session.store import SESSION_ID_PATTERN
from trading_r1.runtime.summarize import summarize_history
from trading_r1.service import preferences, state

_LOG = logging.getLogger(__name__)
router = APIRouter()


class CompactRequest(BaseModel):
    """Request body for compacting a durable session history."""

    session_id: str = Field(
        ...,
        pattern=f"^{SESSION_ID_PATTERN}$",
        description="The session to compact (summarize older history).",
    )
    instruction: str | None = Field(
        None, description="Optional steer for the summary (e.g. 'keep every ticker figure')."
    )
    profile: str | None = Field(None, description="Backbone profile override.")


@router.post("/compact")
async def compact(req: CompactRequest) -> Any:
    """Compact a multi-turn session: LLM-summarize the older history (keeping the
    recent turn verbatim) and swap it in, freeing context. Optional `instruction`
    steers what to preserve. Fail-open (returns compacted=false on a no-op/error)."""
    profile = state.resolve_profile(req.profile)
    async with state.session_lock(req.session_id):  # after a running turn, not alongside it
        history = await asyncio.to_thread(state.SESSIONS.history, req.session_id)
        if not history:
            return {"compacted": False, "reason": "unknown or empty session"}
        with recorder.bind(state.SESSIONS.recording(req.session_id)):
            new_history, stats = await summarize_history(
                history, instruction=req.instruction, profile=profile
            )
        if stats.get("compacted"):
            await asyncio.to_thread(
                state.SESSIONS.compact, req.session_id, new_history, stats, trigger="user"
            )
        return stats


@router.get("/sessions")
async def sessions(
    limit: int = Query(50, ge=1, le=200), include_jobs: bool = False
) -> dict[str, Any]:
    """List saved sessions (most recently active first, at most ``limit``) for a
    pick-from-a-list UX. Summaries only (id, title, timestamps, turn count), no message
    bodies. The sessions of scheduled runs (``job-*``) are left out unless ``include_jobs``.
    ``sync`` is each session's state in the archive: synced, pending, full, error or off."""
    found = await asyncio.to_thread(state.SESSIONS.list, limit, include_jobs=include_jobs)
    try:
        marks = state.sync().states([s["id"] for s in found])
    except Exception:  # noqa: BLE001 (sync trouble never hides the sessions)
        _LOG.exception("the sync state of the sessions could not be read")
        marks = {s["id"]: "error" for s in found}
    return {"sessions": [{**s, "sync": marks[s["id"]]} for s in found]}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a session: its log here, and its copy in the archive (``sync`` says whether the
    archive confirmed: ``deleted``, ``pending`` while it has not, ``off`` if it never held it).
    Waits for a running turn of the session. A session neither place holds is a 404."""
    async with state.session_lock(session_id):
        existed = await asyncio.to_thread(state.SESSIONS.exists, session_id)
        await asyncio.to_thread(state.SESSIONS.delete, session_id)
    try:
        sync = await state.sync().forget(session_id)
    except Exception:  # noqa: BLE001 (the session is gone here; sync trouble does not undo that)
        _LOG.exception("the archive was not told that %s was deleted", session_id)
        sync = "error"
    if not existed and sync == "off":
        raise HTTPException(status_code=404, detail=f"no session {session_id!r}")
    return {"deleted": True, "sync": sync}


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str) -> dict[str, Any]:
    """A session as the user saw it, for resuming or switching to it: each turn's question,
    config, the events to replay and how it ended, with compactions in place. Empty for an
    unknown session."""
    return {"id": session_id, "turns": await asyncio.to_thread(state.SESSIONS.turns, session_id)}


@router.get("/sessions/{session_id}/report")
async def session_report(session_id: str, turn: int | None = None) -> HTMLResponse:
    """The HTML report of a session's turn (the last answered one by default). A 404 when the
    session has no answered turn to report."""
    snapshot = await asyncio.to_thread(snapshot_from_session, state.SESSIONS, session_id, turn)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no answered turn to report")
    return HTMLResponse(render_html(snapshot))


@router.get("/status")
async def status(session_id: str | None = None, profile: str | None = None) -> dict[str, Any]:
    """Context usage for a session (token count vs window), with the system prompt and tool
    schemas a request carries: the /status command and the sidebar meter. ``used_pct`` is of the
    configured context window; the model label reflects the ``profile`` (the explicit selection
    when the request omits it; 400 if none is selected)."""
    cfg = preferences.run_config()
    backbone = await with_context_window(state.resolve_profile(profile))
    window = window_of(backbone, cfg.recovery)
    history = await asyncio.to_thread(state.SESSIONS.history, session_id)
    used = estimate_tokens(history) + await asyncio.to_thread(state.prompt_overhead_of, session_id)
    return {
        "model": backbone.model,
        "context_window": window,
        "compact_at": compact_at(backbone, cfg.recovery),
        "tokens_used": used,
        "used_pct": round(100 * used / window, 1) if window else 0.0,
        "turns": await asyncio.to_thread(state.SESSIONS.turn_count, session_id)
        if session_id
        else 0,
    }
