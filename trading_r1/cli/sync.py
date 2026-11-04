# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 sync`: the copy of your sessions in your Tauric account.

The running backend does the work (it keeps sending in the background); this command only talks
to it. Subcommands: status, on, off, push, import, pull [ID [--as NEW-ID]].
Exit status 0 on success, 1 when the backend is unreachable or refuses.
"""

from __future__ import annotations

import argparse
import sys

from trading_r1.cli.client import call_backend
from trading_r1.host.instance import ServiceUnreachableError, find_backend
from trading_r1.state.settings import SettingsStore


def _size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def _show(status: dict) -> None:
    print(f"sync: {status['state']}")
    counts = status["counts"]
    parts = [f"{counts[k]} {k}" for k in ("synced", "pending", "full", "error") if counts[k]]
    if parts:
        print("sessions: " + ", ".join(parts))
    if status.get("deleting"):
        print(f"deleted here, not yet confirmed there: {status['deleting']}")
    usage = status.get("usage")
    if usage:
        print(f"account: {_size(usage['used_bytes'])} of {_size(usage['allowance_bytes'])}")
    if status.get("error"):
        print(f"last problem: {status['error']}")


def _status(_args: argparse.Namespace) -> int:
    status = call_backend("GET", "/sync")
    if status is None:
        return 1
    _show(status)
    return 0


def _switch(args: argparse.Namespace) -> int:
    # the switch needs no backend: starting one just to turn sending off would send
    try:
        running = find_backend()
    except ServiceUnreachableError:
        running = None
    if running is None:
        SettingsStore().update({"sync": args.enabled})
    elif call_backend("PUT", "/settings", {"sync": args.enabled}) is None:
        return 1
    print("sync is on" if args.enabled else "sync is off: nothing is sent")
    return 0


def _push(_args: argparse.Namespace) -> int:
    status = call_backend("POST", "/sync/push")
    if status is None:
        return 1
    _show(status)
    return 0


def _import(_args: argparse.Namespace) -> int:
    status = call_backend("POST", "/sync/import")
    if status is None:
        return 1
    print(f"enrolled {status['enrolled']} existing session(s)")
    _show(status)
    return 0


def _pull(args: argparse.Namespace) -> int:
    if args.id:
        body = {"id": args.id} | ({"as": args.as_id} if args.as_id else {})
        done = call_backend("POST", "/sync/restore", body)
        if done is None:
            return 1
        print(f"restored {args.id}" + (f" as {done['id']} (not synced)" if args.as_id else ""))
        return 0
    result = call_backend("GET", "/sync/remote")
    if result is None:
        return 1
    if not result["sessions"]:
        print("all the sessions in your account are already on this machine", file=sys.stderr)
    for s in result["sessions"]:
        print(f"{s['id']}  {_size(s['size'])}  {s.get('label') or ''}".rstrip())
    return 0


_DETAILS = """\
Only sessions you start in the terminal are copied, from their first question. Scheduled
reports' sessions and Telegram chats are never sent; sessions already on this machine are not
copied until sync import.

The session list marks each session: ✓ copied, … waiting, ! your account's allowance is used up,
✗ stopped. Refused keys, rate limits, a full account and lost connections are retried; the
local record is never changed.

Each session continues in one place at a time. If you continue it on two devices, the second to
sync stops: nothing is merged or lost, but that session no longer syncs. To keep your account's
version as a new, unsynced session: sync pull ID --as NEW-ID."""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="trading-r1 sync",
        description="Your sessions, copied to your Tauric account.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DETAILS,
    )
    sub = ap.add_subparsers(required=True)
    sub.add_parser(
        "status", help="whether sync is on, what is sent and how much of your account is used"
    ).set_defaults(handler=_status)
    for name, enabled, summary in (
        ("on", True, "start copying new sessions"),
        ("off", False, "stop: nothing more is sent"),
    ):
        sub.add_parser(name, help=summary).set_defaults(handler=_switch, enabled=enabled)
    sub.add_parser("push", help="send what is waiting now").set_defaults(handler=_push)
    sub.add_parser("import", help="also copy the sessions already on this machine").set_defaults(
        handler=_import
    )
    pull = sub.add_parser("pull", help="list the sessions only your account has, or get one back")
    pull.add_argument("id", nargs="?", help="the session to restore (omit to list)")
    pull.add_argument(
        "--as",
        dest="as_id",
        metavar="NEW-ID",
        help="keep it as a new session of this id, on this machine only",
    )
    pull.set_defaults(handler=_pull)
    args = ap.parse_args(argv)
    return args.handler(args)
