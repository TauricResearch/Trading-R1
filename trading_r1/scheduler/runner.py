# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The scheduler: once in a while, claim what has fallen due and run it.

It runs inside the backend that holds the backend lock, which is also what makes it the only
scheduler. A fire that fell due within the grace window runs; if several fell due (the backend
was down, the machine slept) only the latest is considered and the older ones are recorded as
missed, never replayed. A run that is already going makes the next fire ``skipped``. Every run
reserves tokens against a daily ceiling before it starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from trading_r1.scheduler.schedule import Schedule
from trading_r1.scheduler.store import Job, JobStore, now
from trading_r1.state.runtime_config import runtime_config

_LOG = logging.getLogger(__name__)
STOP_S = 8.0  # what a stop gives the runs in flight to record themselves
WATCH_S = 2.0  # how often a run's spending is looked at

LOOK_BACK = timedelta(days=7)  # fires older than this are not even listed as missed
TICK_S = 20.0
# What runs a job: given the job and its run id, do the research and the delivery, and return
# (status, termination, tokens, error, delivery outcomes).
Execute = Callable[[Job, int], Awaitable["RunResult"]]
# the job, its runs that never finished, the last reason; whether the notice went out
Notify = Callable[[Job, list[int], str], Awaitable[bool]]
TELL_AGAIN_S = 600.0  # a notice that could not go out is tried again this often


def tokens_of(usage: dict[str, dict[str, int]]) -> int:
    return sum(u["prompt"] + u["completion"] for u in usage.values())


async def watch_tokens(task: asyncio.Future, spent: dict[str, dict[str, int]], limit: int) -> bool:
    """Wait for ``task``; cancel it once ``spent`` passes ``limit`` tokens. True if it was
    cancelled for that. If the caller is cancelled, so is the task."""
    over = False
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=WATCH_S)
            if not task.done() and tokens_of(spent) > limit:
                over = True
                task.cancel()
                await asyncio.wait({task})
    except asyncio.CancelledError:
        task.cancel()
        raise
    return over


class RunResult:
    def __init__(
        self,
        status: str,
        termination: str = "",
        tokens: int | None = None,
        error: str = "",
        delivery: list | None = None,
    ) -> None:
        self.status, self.termination, self.tokens = status, termination, tokens
        self.error, self.delivery = error, delivery or []


class Scheduler:
    def __init__(self, store: JobStore, execute: Execute, notify: Notify | None = None) -> None:
        self.store = store
        self._execute = execute
        self._notify = notify
        self._tell_again: dict[str, float] = {}
        self._slots = asyncio.Semaphore(runtime_config().jobs.concurrency)
        self._tasks: set[asyncio.Task] = set()
        self._running: set[int] = set()  # run ids claimed and not yet finished
        self._loop_task: asyncio.Task | None = None

    # ----- lifecycle ------------------------------------------------------------------ #

    def start(self) -> None:
        recovered = self.store.recover(now())
        if recovered:
            _LOG.warning("%d run(s) were interrupted by the last stop", recovered)
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self, timeout: float = STOP_S) -> None:
        """Stop ticking and cancel the runs in flight (each records itself ``interrupted``)."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=timeout)
        for run_id in list(self._running):  # cancelled before they began, or past the timeout
            self.store.finish(run_id, "interrupted", now(), error="the backend was stopping")
            self._running.discard(run_id)

    async def _loop(self) -> None:
        while True:
            try:
                for job, run_id in await asyncio.to_thread(self.plan, now()):
                    self.spawn(job, run_id)
                await self.tell_unrun()
            except Exception:  # noqa: BLE001 (one bad tick must not end the scheduler)
                _LOG.exception("scheduler tick failed")
            await asyncio.sleep(TICK_S)

    async def tell_unrun(self) -> None:
        """Tell each job's channels about its runs that never finished, so a report that stops
        arriving is explained. A chat's runs and those of a job since deleted have nobody to tell.
        A notice that could not go out (no network yet, say) is tried again."""
        if self._notify is None:
            return
        rows = await asyncio.to_thread(self.store.unrun, now())
        by_job: dict[str, list[tuple[int, str]]] = {}
        for run_id, job_id, error in rows:
            by_job.setdefault(job_id, []).append((run_id, error))
        for job_id, runs in by_job.items():
            if time.monotonic() < self._tell_again.get(job_id, 0.0):
                continue
            job = await asyncio.to_thread(self.store.get, job_id)
            if job is None or await self._notify(job, [r[0] for r in runs], runs[-1][1]):
                await asyncio.to_thread(self.store.mark_noticed, [r[0] for r in runs])
                self._tell_again.pop(job_id, None)
            else:
                self._tell_again[job_id] = time.monotonic() + TELL_AGAIN_S

    # ----- one tick ------------------------------------------------------------------- #

    def plan(self, at: datetime) -> list[tuple[Job, int]]:
        """Claim what has fallen due at ``at``; the runs to start. Pure storage work, so it can
        run off the event loop."""
        claimed: list[tuple[Job, int]] = []
        for job in self.store.list():
            if not job.enabled:
                continue
            try:
                run_id = self._claim_due(job, at)
            except Exception:  # noqa: BLE001 (one bad job must not stop the others)
                _LOG.exception("job %s could not be planned", job.id)
                continue
            if run_id is not None:
                claimed.append((job, run_id))
        return claimed

    def _claim_due(self, job: Job, at: datetime) -> int | None:
        """Claim what of ``job`` has fallen due; the run to start, if any."""
        schedule = Schedule(job.kind, job.cron, job.tz, job.run_at)
        start = max(datetime.fromisoformat(job.cursor), at - LOOK_BACK)
        fires = []
        for fire in schedule.after(start):
            if fire > at:
                break
            fires.append(fire)
        if not fires:
            return None
        late = at - fires[-1] > timedelta(seconds=runtime_config().jobs.late_grace_s)
        claim = self.store.claim(
            job,
            fires,
            status_of_latest="missed" if late else "running",
            reserved=runtime_config().jobs.run_tokens,
            ceiling=runtime_config().jobs.daily_tokens,
            at=at,
            error="it fell due while no backend ran" if late else "",
        )
        return claim[0] if claim is not None and claim[1] == "running" else None

    def tick(self, at: datetime) -> list[int]:
        """Plan and start the runs due at ``at``; returns their ids."""
        claimed = self.plan(at)
        for job, run_id in claimed:
            self.spawn(job, run_id)
        return [run_id for _, run_id in claimed]

    def claim_manual(self, job: Job, at: datetime) -> tuple[int, str]:
        """Claim a run the owner asked for (storage work: off the event loop). Returns
        ``(run id, status)``; a ``running`` one is then started with :meth:`spawn`."""
        return self.store.claim_manual(
            job,
            reserved=runtime_config().jobs.run_tokens,
            ceiling=runtime_config().jobs.daily_tokens,
            at=at,
        )

    def slot(self) -> asyncio.Semaphore:
        """The concurrency limit, for work outside the jobs that counts against it."""
        return self._slots

    # ----- one run -------------------------------------------------------------------- #

    def spawn(self, job: Job, run_id: int) -> None:
        """Start a claimed run."""
        self._running.add(run_id)
        task = asyncio.create_task(self._run(job, run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job, run_id: int) -> None:
        try:
            try:
                async with self._slots:
                    result = await self._execute(job, run_id)
            except asyncio.CancelledError:
                await self._finish(run_id, "interrupted", error="the backend was stopping")
                raise
            except Exception as err:  # noqa: BLE001 (recorded, never raised into the loop)
                _LOG.exception("job %s run %s failed", job.id, run_id)
                await self._finish(run_id, "failed", error=f"{type(err).__name__}: {err}")
                return
            await self._finish(
                run_id,
                result.status,
                termination=result.termination,
                tokens=result.tokens,
                error=result.error,
                delivery=result.delivery,
            )
        finally:
            self._running.discard(run_id)

    async def _finish(self, run_id: int, status: str, **record: Any) -> None:
        """Record how a run ended. If that fails, ``failed`` is recorded instead, which frees the
        job and its reservation; a failure of that too is logged, never raised."""
        for final, fields in (
            (status, record),
            ("failed", {"error": "the result could not be recorded"}),
        ):
            try:
                await asyncio.to_thread(self.store.finish, run_id, final, now(), **fields)
                return
            except Exception:  # noqa: BLE001
                _LOG.exception("run %s: recording %r failed", run_id, final)
