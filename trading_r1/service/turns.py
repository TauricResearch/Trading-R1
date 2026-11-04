# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""One turn of a session, the same from the terminal, a chat or a schedule: one at a time per
session, compacted when it no longer fits, recorded as it goes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from trading_r1.providers import ModelProfile, get_profile
from trading_r1.providers.discovery import with_context_window
from trading_r1.providers.health import ensure_local_ready
from trading_r1.research import run_logged
from trading_r1.runtime.auto_compact import compact_before_turn
from trading_r1.service import preferences, state
from trading_r1.service.events import SSE_VERSION
from trading_r1.state.runtime_config import runtime_config

_LOG = logging.getLogger(__name__)


async def session_turn(
    session_id: str,
    question: str,
    *,
    profile: ModelProfile,
    mode: str | None,
    run: Any,
    on_event: Any = None,
    **run_kw: Any,
) -> dict[str, Any]:
    """Run one turn of ``session_id``. A turn starts from the context and evidence ids the previous
    one left, so a session runs one at a time."""
    async with state.session_lock(session_id):
        config = preferences.run_config()
        # a dead local backend fails here in seconds, not after compaction's retries
        if run_kw.get("client") is None and profile.reader_profile:
            await ensure_local_ready(profile, get_profile(profile.reader_profile))
        try:
            async with asyncio.timeout(runtime_config().limits.per_call_timeout_s):
                await compact_before_turn(
                    state.SESSIONS,
                    session_id,
                    question,
                    profile=await with_context_window(profile),
                    config=config,
                    emit=on_event or (lambda _kind, _data: None),
                    overhead=await asyncio.to_thread(state.prompt_overhead_of, session_id, mode),
                )
        except TimeoutError:  # the turn goes on with the context it has
            _LOG.warning("compacting %s took too long; the turn goes on", session_id)
        if not await asyncio.to_thread(state.SESSIONS.turn_count, session_id):
            try:
                state.sync().enrol(session_id)  # the first turn, while sync is on
            except Exception:  # noqa: BLE001 (sync trouble never fails a turn)
                _LOG.exception("session %s was not enrolled for sync", session_id)
        try:
            return await run_logged(
                state.SESSIONS,
                session_id,
                question,
                profile=profile,
                mode=mode,
                run_start={"version": SSE_VERSION},
                on_event=on_event,
                run=run,
                memory=state.MEMORY,
                config=config,
                **run_kw,
            )
        finally:
            state.sync_kick()
