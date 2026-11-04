# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Automatic compaction of a session's history, before a turn.

A run trims old tool results on its own as it nears the window (`ModelCaller.turn`). What a run
cannot do is shorten the history it starts from: when the earlier turns of a session already
fill most of the window, they are summarized (the `/compact` operation) before the new question
is asked, and the client is told, with the tokens before and after. Compaction that fails is
reported and the turn goes on; after two failures in a session it stops trying for that session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trading_r1.contract import events
from trading_r1.providers import ModelProfile
from trading_r1.runtime.context_budget import compact_at, estimate_tokens
from trading_r1.runtime.session import recorder
from trading_r1.runtime.session.store import SessionStore
from trading_r1.runtime.summarize import summarize_history
from trading_r1.state.runtime_config import RuntimeConfig

_MAX_FAILURES = 2
_failures: dict[str, int] = {}


async def compact_before_turn(
    store: SessionStore,
    session_id: str,
    question: str,
    *,
    profile: ModelProfile,
    config: RuntimeConfig,
    emit: Callable[[str, dict[str, Any]], None],
    overhead: int = 0,
) -> None:
    """Summarize the session's history when it, with the new question and the ``overhead`` of
    tokens each request carries beside them (system prompt, tool schemas), is past the threshold."""
    threshold = compact_at(profile, config.recovery)
    if threshold is None or _failures.get(session_id, 0) >= _MAX_FAILURES:
        return
    history = store.history(session_id) or []
    before = overhead + estimate_tokens([*history, {"role": "user", "content": question}])
    if not history or before <= threshold:
        return

    events.send(emit, events.Compaction(stage="start", how="summary", tokens_before=before))
    with recorder.bind(store.recording(session_id)):
        new_history, stats = await summarize_history(history, profile=profile, config=config)
    if not stats.get("compacted"):
        _failures[session_id] = _failures.get(session_id, 0) + 1
        events.send(emit, events.Compaction(stage="failed", how="summary", tokens_before=before))
        return
    _failures.pop(session_id, None)
    store.compact(session_id, new_history, stats, trigger="auto")
    after = overhead + estimate_tokens([*new_history, {"role": "user", "content": question}])
    events.send(
        emit,
        events.Compaction(stage="end", how="summary", tokens_before=before, tokens_after=after),
    )
