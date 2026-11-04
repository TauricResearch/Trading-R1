# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Unattended runs: research on a session with nobody to ask, within the per-run token limit.

``run_unattended`` is the one run a scheduled job and a chat message share: metered, logged to
the session, stopped when it spends past its limit, and ending as a report snapshot or as the
reason there is none. ``execute_job`` is the run the scheduler performs: that, then the report
(or a notice that there is none) to the job's channels, once, then the pruning of the job's
oldest session logs (the run records stay: they hold the usage accounting).
"""

from __future__ import annotations

import asyncio
import logging
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime

from trading_r1.delivery import (
    Message,
    Outbox,
    build_channels,
    deliver,
    failure_message,
    report_message,
)
from trading_r1.providers import get_profile
from trading_r1.providers.usage import metered
from trading_r1.report import ReportSnapshot, snapshot_from_session
from trading_r1.research import run_research
from trading_r1.runtime.run_state import RunPolicy
from trading_r1.runtime.session import recorder
from trading_r1.runtime.session.store import is_completed, is_cut_off
from trading_r1.scheduler import Job, RunResult
from trading_r1.scheduler.runner import tokens_of, watch_tokens
from trading_r1.service import preferences, state
from trading_r1.service.turns import session_turn
from trading_r1.state.runtime_config import runtime_config

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Unattended:
    """How an unattended run ended: the report, or why there is none."""

    tokens: int
    termination: str = ""
    snapshot: ReportSnapshot | None = None
    reason: str = ""  # short, for a notice to the owner
    detail: str = ""  # for the run's record


async def run_unattended(
    session_id: str,
    prompt: str,
    *,
    profile: str | None,
    mode: str,
    turn: int | None = None,
    policy: RunPolicy = RunPolicy.UNATTENDED,
    delivery: str = "",
) -> Unattended:
    """One turn of ``session_id`` as an unattended run. Never raises except when the caller is
    cancelled: a failure is the result."""
    try:
        backbone = get_profile(profile)
        if (
            backbone.api_key_literal is None
            and backbone.api_key_env
            and not backbone.resolve_api_key()
        ):
            raise ValueError(f"{backbone.name} needs {backbone.api_key_env}, which is not set")
        preferences.run_config()  # a bad file is the run's result, said here
    except Exception as err:  # noqa: BLE001 (a model that is gone is the run's result)
        _LOG.warning("the unattended run of %s has no model: %s", session_id, err)
        return Unattended(0, reason=str(err)[:200], detail=str(err)[:300])
    with metered() as spent:  # also counted by the run's own meter
        research = asyncio.ensure_future(
            session_turn(
                session_id,
                prompt,
                profile=backbone,
                mode=mode,
                run=run_research,
                policy=policy,
                delivery=delivery,
            )
        )
        try:
            over = await watch_tokens(research, spent, runtime_config().jobs.run_tokens)
            traj = None if research.cancelled() else research.result()
        except asyncio.CancelledError:
            research.cancel()
            raise
        except Exception as err:  # noqa: BLE001 (the run's failure is its result)
            # the error, redacted: an endpoint's address in it may carry a password
            detail = recorder.failure_text(err)
            _LOG.error("the unattended run of %s failed: %s", session_id, detail)
            return Unattended(tokens_of(spent), reason=type(err).__name__, detail=detail)
    if traj is None:  # stopped for spending past its limit
        why = "it used more than its token limit" if over else "it was cancelled"
        return Unattended(tokens_of(spent), reason=why, detail=why)
    tokens = max(tokens_of(spent), tokens_of(traj.get("usage") or {}))
    termination = str(traj.get("termination") or "")
    snapshot = (
        snapshot_from_session(state.SESSIONS, session_id, turn)
        if is_completed(termination) or (is_cut_off(termination) and traj.get("text"))
        else None
    )
    if snapshot is None:
        reason = str(traj.get("error") or termination or "no answer")
        return Unattended(tokens, termination, reason=reason, detail=f"the run ended: {reason}")
    return Unattended(tokens, termination, snapshot)


def _prune_sessions(job: Job) -> None:
    """Delete the session logs of a job's runs beyond the newest ``jobs.sessions_kept``, and clear
    the session of those runs. Touches only this job's own run sessions; never raises."""
    prefix = f"job-{job.id}-"
    try:
        for run_id, session_id in state.jobs().old_sessions(
            job.id, runtime_config().jobs.sessions_kept
        ):
            lock = state.SESSION_LOCKS.get(session_id)
            if lock is not None and lock.locked():  # a reply is running in it: the next prune
                continue
            if session_id.startswith(prefix):
                state.SESSIONS.delete(session_id)
            state.jobs().clear_session(run_id)
    except Exception:  # noqa: BLE001 (housekeeping must not fail the run)
        _LOG.exception("could not prune the sessions of job %s", job.id)


async def notify_unrun(job: Job, run_ids: list[int], reason: str) -> bool:
    """Tell the job's channels that runs of it produced no report; whether that went out. One notice
    a day per job and reason, however often a job's runs are refused for it."""
    channels, problems = build_channels()
    count = "One run" if len(run_ids) == 1 else f"{len(run_ids)} runs"
    next_one = " The next one runs on schedule." if job.kind == "cron" else ""
    message = Message(
        f"{job.name}: no report",
        f"{count} of {job.name!r} produced no report ({reason}). It is not retried.{next_one}",
        name=job.name,
    )
    day = datetime.now(UTC).date().isoformat()
    key = f"unrun-{job.id}-{day}-{zlib.crc32(reason.encode()):08x}"
    box = Outbox()
    await asyncio.to_thread(box.forget_failed, [f"{key}:{name}" for name in job.deliver])
    outcomes = await deliver(box, channels, job.deliver, message, key, problems)
    failed = [o for o in outcomes if o.status == "failed"]
    if failed:
        _LOG.warning("a notice about %s did not go out: %s", job.name, failed[0].detail)
    return not failed


async def execute_job(job: Job, run_id: int) -> RunResult:
    """One scheduled run: research on a fresh session as an unattended run, then the report (or
    a notice that there is none) to the job's channels, once."""
    session_id = f"job-{job.id}-{run_id}"
    state.jobs().set_session(run_id, session_id)
    channels, problems = build_channels()
    run = await run_unattended(
        session_id,
        job.prompt,
        profile=job.profile,
        mode=job.mode,
        turn=1,
        policy=RunPolicy.SCHEDULED,
        delivery=", ".join(job.deliver),
    )
    if run.snapshot is None:
        message = failure_message(job.name, run.reason)
    else:
        message = report_message(
            run.snapshot, name=job.name, job=True, formats=preferences.report_formats()
        )
    # a channel that is not usable says why: its own reason, else the settings file's
    unusable = {
        n: problems.get(n) or problems.get("settings", "") for n in job.deliver if n not in channels
    }
    outcomes = await deliver(
        Outbox(), channels, job.deliver, message, f"run-{run_id}", unusable=unusable
    )
    rows = state.outcome_rows(outcomes)
    unsent = [
        f"{o['channel']} ({o['detail']})" if o.get("detail") else o["channel"]
        for o in rows
        if o["status"] != "sent"
    ]
    await asyncio.to_thread(_prune_sessions, job)
    notes = [run.detail] if run.detail else []
    if unsent:
        notes.append(f"not delivered: {', '.join(unsent)}")
    finished = "undelivered" if unsent else "done"
    return RunResult(
        finished if run.snapshot is not None else "failed",
        termination=run.termination,
        tokens=run.tokens,
        error="; ".join(notes),
        delivery=rows,
    )
