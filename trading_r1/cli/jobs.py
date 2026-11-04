# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 jobs`: reports that run on a schedule and arrive in your channels.

Jobs live in the running backend, which runs them (`trading-r1 daemon install` keeps it running);
this command only talks to it.

Subcommands: add, list, remove ID, enable ID, disable ID, run ID, runs ID.
Exit status 0 on success, 1 when the backend is unreachable or refuses.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from trading_r1.cli.client import call_backend
from trading_r1.runtime.modes import MODES
from trading_r1.scheduler.schedule import EVERY, WEEKDAYS

NOT_KEPT_RUNNING = (
    "(reports run only while this backend runs; to keep them running: trading-r1 daemon install)"
)


def _show(job: dict) -> str:
    when = job["when"]
    state = "" if job["enabled"] else "  [disabled]"
    nxt = f"  next {job['next']}" if job.get("next") else ""
    to = ",".join(job["deliver"]) or "no channel"
    return f"{job['id']}  {job['name']}  {when}  -> {to}  ({job['profile']}){state}{nxt}"


def _local_zone() -> str:
    """This machine's zone, as the terminal uses: ``TZ``, else the system's, else UTC."""
    if zone := os.environ.get("TZ", "").lstrip(":"):
        return zone
    _, found, name = str(Path("/etc/localtime").resolve()).partition("zoneinfo/")
    return "UTC" if not found or name == "Etc/UTC" else name


def _add(args: argparse.Namespace) -> int:
    body = {
        "name": args.name,
        "prompt": args.prompt,
        "tz": args.tz,
        "deliver": [c for c in args.deliver.split(",") if c],
        "mode": args.mode,
        **(
            {"cron": args.cron}
            if args.cron
            else {"every": args.every, "time": args.time, "weekday": args.weekday}
            if args.every
            else {"at": args.at}
        ),
        **({"profile": args.profile} if args.profile else {}),
    }
    job = call_backend("POST", "/jobs", body)
    if job is None:
        return 1
    print(_show(job))
    if not job.get("kept_running"):
        print(NOT_KEPT_RUNNING, file=sys.stderr)
    return 0


def _list(_args: argparse.Namespace) -> int:
    result = call_backend("GET", "/jobs")
    if result is None:
        return 1
    for job in result["jobs"]:
        print(_show(job))
    if not result["scheduler"]:
        print("(this backend is not running the scheduler: jobs do not fire)", file=sys.stderr)
    elif not result.get("kept_running") and result["jobs"]:
        print(NOT_KEPT_RUNNING, file=sys.stderr)
    return 0


def _remove(args: argparse.Namespace) -> int:
    return 0 if call_backend("DELETE", f"/jobs/{args.id}") else 1


def _enable(args: argparse.Namespace) -> int:
    body = {"enabled": args.enabled}
    return 0 if call_backend("POST", f"/jobs/{args.id}/enabled", body) else 1


def _run(args: argparse.Namespace) -> int:
    started = call_backend("POST", f"/jobs/{args.id}/run")
    if started is None:
        return 1
    print(f"run {started['run']}: {started['status']}")
    return 0


def _runs(args: argparse.Namespace) -> int:
    result = call_backend("GET", f"/jobs/{args.id}/runs")
    if result is None:
        return 1
    for r in result["runs"]:
        note = f"  {r['error']}" if r["error"] else ""
        sent = ",".join(f"{d['channel']}:{d['status']}" for d in r["delivery"])
        print(f"{r['id']}  {r['occurrence']}  {r['status']}  {sent}{note}")
    return 0


_LIMITS = """\
Jobs run unattended on the model current when you added them: they cannot ask you a question or
change what the agent remembers about you. Fires due while the backend was down are recorded as
missed, unless within 30 minutes of their time; one that comes while the last run is still going
is skipped. Jobs fire at least 15 minutes apart and at most two run at once. Each run stops at
1,500,000 tokens (TR1_JOBS_RUN_TOKENS), and none starts once the last 24 hours used 6,000,000
(TR1_JOBS_DAILY_TOKENS). Runs with no report send a short notice instead; those whose report did
not reach a channel show as undelivered."""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="trading-r1 jobs",
        description="Scheduled reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_LIMITS,
    )
    sub = ap.add_subparsers(required=True)
    add = sub.add_parser("add", help="schedule a prompt")
    add.set_defaults(handler=_add)
    add.add_argument("--name", required=True)
    add.add_argument("--prompt", required=True, help="the question to research each time")
    when = add.add_mutually_exclusive_group(required=True)
    when.add_argument("--cron", help="five fields, e.g. '0 8 * * 1-5' (weekdays at 08:00)")
    when.add_argument("--every", choices=EVERY, help="with --time")
    when.add_argument("--at", help="one time, ISO with an offset, e.g. 2026-11-02T08:00:00-05:00")
    add.add_argument("--time", help="time of day for --every, HH:MM")
    add.add_argument("--weekday", choices=WEEKDAYS)
    add.add_argument("--tz", default=_local_zone(), help="zone (default: this machine's)")
    add.add_argument("--deliver", default="", help="channels, e.g. email,telegram")
    add.add_argument("--profile", default=None, help="the model (default: the current one)")
    add.add_argument("--mode", default="auto", choices=list(MODES))
    sub.add_parser("list", help="the jobs").set_defaults(handler=_list)
    for action, handler, defaults, summary in (
        ("remove", _remove, {}, "delete a job"),
        ("enable", _enable, {"enabled": True}, "turn a job on"),
        ("disable", _enable, {"enabled": False}, "turn a job off"),
        ("run", _run, {}, "run a job now"),
        ("runs", _runs, {}, "what each run of a job did"),
    ):
        parser = sub.add_parser(action, help=summary)
        parser.add_argument("id")
        parser.set_defaults(handler=handler, **defaults)
    args = ap.parse_args(argv)
    return args.handler(args)
