# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Job routes: scheduled research runs (list, add, delete, enable or disable, run now, run
history). ``run now`` needs the scheduler this backend owns (``state.SCHEDULER``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from trading_r1.delivery import build_channels
from trading_r1.host import daemon
from trading_r1.runtime.modes import resolve_mode
from trading_r1.scheduler import Schedule, ScheduleError
from trading_r1.service import state
from trading_r1.state.runtime_config import runtime_config

router = APIRouter()


class JobRequest(BaseModel):
    """A job to schedule: a prompt, when to run it, and where to send the report."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    prompt: str = Field(..., min_length=1, max_length=8000)
    cron: str | None = Field(None, description="Five fields, e.g. '0 8 * * 1-5'.")
    tz: str = Field("UTC", description="The time zone the cron expression is read in.")
    at: str | None = Field(
        None,
        description="One time instead of a cron: 'YYYY-MM-DD HH:MM' in tz, or ISO with an offset.",
    )
    every: str | None = Field(None, description="day, weekdays or week, with time (and weekday).")
    time: str | None = Field(None, description="Time of day, HH:MM, for 'every'.")
    weekday: str | None = Field(None, description="mon..sun, for every='week'.")
    deliver: list[str] = Field(default_factory=list, description="Channels for the report.")
    profile: str | None = Field(None, description="The model to run on (default: the current).")
    mode: str = Field("auto")


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


def _job_view(job: Any) -> dict[str, Any]:
    schedule = Schedule(job.kind, job.cron, job.tz, job.run_at)
    following = next(schedule.after(datetime.now(UTC)), None) if job.enabled else None
    return {
        **job.public(),
        "when": schedule.describe(),
        "next": following.isoformat(timespec="seconds") if following else None,
    }


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    store = state.jobs()
    jobs = await asyncio.to_thread(store.list)
    last = {j.id: (await asyncio.to_thread(store.runs, j.id, 1)) for j in jobs}
    return {
        "jobs": [{**_job_view(j), "last_run": (last[j.id] or [None])[0]} for j in jobs],
        "scheduler": state.SCHEDULER is not None,
        "kept_running": await asyncio.to_thread(daemon.kept_running),
        "usage": {
            "day_tokens": await asyncio.to_thread(store.tokens_last_day, datetime.now(UTC)),
            "day_limit": runtime_config().jobs.daily_tokens,
            "run_limit": runtime_config().jobs.run_tokens,
        },
    }


@router.post("/jobs")
async def add_job(req: JobRequest) -> dict[str, Any]:
    if sum(x is not None for x in (req.cron, req.at, req.every)) != 1:
        raise HTTPException(status_code=422, detail="give either cron, at or every")
    try:
        if req.every is not None:
            schedule = Schedule.preset(req.every, req.time or "", req.weekday, req.tz)
        elif req.cron is not None:
            schedule = Schedule("cron", cron=req.cron, tz=req.tz)
        else:
            schedule = Schedule.once(req.at or "", req.tz)
        schedule.validate(datetime.now(UTC))
    except ScheduleError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    configured, _ = build_channels()
    missing = [c for c in req.deliver if c not in configured]
    if missing:
        raise HTTPException(status_code=422, detail=f"not a usable channel: {', '.join(missing)}")
    profile = state.resolve_profile(req.profile)
    job = await asyncio.to_thread(
        state.jobs().add,
        {
            "name": req.name,
            "prompt": req.prompt,
            "kind": schedule.kind,
            "cron": schedule.cron,
            "tz": schedule.tz,
            "run_at": schedule.run_at,
            "profile": profile.name,
            "mode": resolve_mode(req.mode),
            "deliver": req.deliver,
        },
        datetime.now(UTC),
    )
    return {**_job_view(job), "kept_running": await asyncio.to_thread(daemon.kept_running)}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, bool]:
    if not await asyncio.to_thread(state.jobs().delete, job_id):
        raise HTTPException(status_code=404, detail="no such job")
    return {"deleted": True}


@router.post("/jobs/{job_id}/enabled")
async def enable_job(job_id: str, req: EnabledRequest) -> dict[str, bool]:
    if not await asyncio.to_thread(
        state.jobs().set_enabled, job_id, req.enabled, datetime.now(UTC)
    ):
        raise HTTPException(status_code=404, detail="no such job")
    return {"enabled": req.enabled}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str) -> dict[str, Any]:
    """Run a job now, in the background (its report goes to its channels)."""
    job = await asyncio.to_thread(state.jobs().get, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    if state.SCHEDULER is None:
        raise HTTPException(status_code=409, detail="this backend is not running the scheduler")
    run_id, status = await asyncio.to_thread(state.SCHEDULER.claim_manual, job, datetime.now(UTC))
    if status == "running":
        state.SCHEDULER.spawn(job, run_id)
    why = ""
    if status != "running":  # skipped: the run's record says why
        why = next((r["error"] for r in state.jobs().runs(job_id, 1) if r["id"] == run_id), "")
    return {"run": run_id, "status": status, "reason": why}


@router.get("/jobs/{job_id}/runs")
async def job_runs(job_id: str) -> dict[str, Any]:
    if await asyncio.to_thread(state.jobs().get, job_id) is None:
        raise HTTPException(status_code=404, detail="no such job")
    return {"runs": await asyncio.to_thread(state.jobs().runs, job_id)}
