# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Research entry point: assemble the harness and run one turn.

``run_research`` is the "ask a question, get a cited report" helper; ``run_logged`` runs it as
a turn of a session and records it in the session log. The full surface (web + market data +
compute tools + bundled skills) is available every run (two-tier disclosure); the mode picks
the prompt variant, not the toolset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from trading_r1.assembly import build_tool_registry, default_skill_registry, run_binder
from trading_r1.providers import ModelProfile, get_profile
from trading_r1.providers.discovery import with_context_window
from trading_r1.providers.health import ensure_local_ready
from trading_r1.runtime.agent import Agent
from trading_r1.runtime.evidence import EvidenceStore
from trading_r1.runtime.memory import MemoryStore
from trading_r1.runtime.modes import get_mode, resolve_mode
from trading_r1.runtime.run_state import RunPolicy, Termination
from trading_r1.runtime.session import recorder
from trading_r1.runtime.session.store import SessionStore, TurnRecorder
from trading_r1.runtime.sources import source_scope
from trading_r1.state.runtime_config import RuntimeConfig, load_runtime_config
from trading_r1.tools.web import FetchCache, citation_finalizer
from trading_r1.tools.web.cache import Citation


async def run_research(
    question: str,
    *,
    profile: ModelProfile,
    reader_profile: ModelProfile | None = None,
    history: list[dict[str, Any]] | None = None,
    mode: str | None = None,
    client: Any | None = None,
    config: RuntimeConfig | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    stream_tokens: bool = False,
    elicit_timeout_s: float = 0.0,
    memory: MemoryStore | None = None,
    evidence: EvidenceStore | None = None,
    policy: RunPolicy = RunPolicy.INTERACTIVE,
    delivery: str = "",
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a deep-research trajectory end-to-end and return it (with citations).

    ``profile`` is the orchestrator (the caller chooses it; none is auto-selected);
    ``reader_profile`` is the cheaper/faster model `visit` uses. ``memory`` is the
    user's notes; ``evidence`` is the session's evidence store (a run without one starts its
    own).
    """
    # Mode selects the prompt variant only: every mode has the same tool registry, so
    # switching mode mid-session never strands history that references a tool.
    mode_spec = get_mode(mode)

    # Pair the reader with the backbone (cloud backbones read with a cloud reader, so a run
    # never depends on the local reader being served). Explicit override wins; otherwise the
    # backbone must *declare* its reader, resolved strictly (no default), so a misconfigured
    # profile fails here.
    if reader_profile is None:
        if not profile.reader_profile:
            raise ValueError(f"profile {profile.name!r} declares no reader_profile")
        reader_profile = get_profile(profile.reader_profile)
    # A dead local backend (a dropped tunnel) fails here in seconds, not after the retry budget.
    # An injected client owns its transport, so the profile's URL isn't what it would reach.
    if client is None:
        await ensure_local_ready(profile, reader_profile)
    config = config or load_runtime_config()
    profile = await with_context_window(profile)  # ask the server for its window, if it says
    cache = FetchCache()
    cache.seed([Citation(**source) for source in sources or []])  # a conversation's numbers last
    skills = default_skill_registry()
    agent = Agent(
        profile=profile,
        registry=build_tool_registry(cache, reader_profile, client=client, config=config),
        config=config,
        client=client,
        finalize=citation_finalizer(cache),
        bind=run_binder(skills),
        skill_registry=skills,
        stream_tokens=stream_tokens,
        # One backbone pass over the draft, when the user turned it on: the model rereads its
        # report against the sources it read and grounds any claim it can't support.
        grounding_review=config.grounding.enabled,
        mode=mode_spec,
        # >0 only for an interactive (streaming) client that can answer an `ask_user`; a
        # headless run leaves it 0 so clarifications auto-resolve to the recommended option.
        elicit_timeout_s=elicit_timeout_s,
        memory=memory,
        evidence=evidence,
        policy=policy,
        delivery=delivery,
    )
    with source_scope(cache):
        traj = await agent.run(question, on_event=on_event, history=history)
    traj["citations"] = [
        {"index": c.index, "url": c.url, "title": c.title, "record": c.record}
        for c in cache.citations
    ]
    return traj


async def run_logged(
    store: SessionStore,
    session_id: str,
    question: str,
    *,
    profile: ModelProfile,
    mode: str | None = None,
    run_start: dict[str, Any] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    run: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    **run_kw: Any,
) -> dict[str, Any]:
    """Run one turn of session ``session_id`` and record it in the session log
    (``runtime/session/store.py``). The turn
    starts from the context and evidence the session's earlier turns left; ``run_start`` is the
    first event logged (what a client's replay folds from). A failure or cancellation is
    logged as the turn's end, then re-raised. ``run`` replaces :func:`run_research` (a test
    seam)."""
    import asyncio

    from trading_r1 import __version__

    config = {
        "profile": profile.name,
        "model": profile.model,
        "mode": resolve_mode(mode),
        "version": __version__,
    }

    def start() -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, TurnRecorder]:
        # the log is read whole: off the event loop, which keeps serving other requests
        return (
            store.evidence(session_id),
            store.sources(session_id),
            store.history(session_id),
            store.begin(session_id, question, config),
        )

    records, sources, history, rec = await asyncio.to_thread(start)
    evidence = EvidenceStore.from_list(records)
    rec("run_start", {"question": question, "mode": config["mode"], **(run_start or {})})

    def sink(kind: str, data: dict[str, Any]) -> None:
        rec(kind, data)
        if on_event is not None:
            on_event(kind, data)

    stop_logging = evidence.observe(rec.record_evidence)  # each record is logged as it is made
    try:
        with recorder.bind(rec):  # every model and tool call of the run is recorded
            traj = await (run or run_research)(
                question,
                profile=profile,
                mode=mode,
                history=history,
                evidence=evidence,
                on_event=sink,
                sources=sources,
                **run_kw,
            )
    except BaseException as exc:  # recorded, then re-raised: the log keeps every turn
        cancelled = isinstance(exc, asyncio.CancelledError)
        if not cancelled:
            rec("error", {"error": recorder.redact(f"{type(exc).__name__}: {exc}")})
        rec.end(None, termination=Termination.CANCELLED if cancelled else Termination.ERROR)
        raise
    finally:
        stop_logging()
    rec.end(traj)
    return traj
