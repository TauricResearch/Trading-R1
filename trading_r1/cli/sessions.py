# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 sessions`: the sessions saved on this machine.

The running backend does the work; this command only talks to it.
Subcommands: list, delete ID. Deleting a session also deletes its copy in your Tauric account.
Exit status 0 on success, 1 when the backend is unreachable or refuses.
"""

from __future__ import annotations

import argparse

from trading_r1.cli.client import call_backend

_SYNC_MARK = {"synced": "✓", "pending": "…", "full": "!", "error": "✗", "off": " "}


def _list(args: argparse.Namespace) -> int:
    result = call_backend("GET", f"/sessions?limit={args.limit}")
    if result is None:
        return 1
    for s in result["sessions"]:
        print(f"{_SYNC_MARK.get(s['sync'], ' ')} {s['id']}  {s.get('title') or ''}".rstrip())
    return 0


_DELETED = {
    "deleted": "deleted here and in your account",
    "pending": "deleted here; the copy in your account is deleted once sync is on and connected",
    "off": "deleted here (it was never copied to your account)",
}


def _delete(args: argparse.Namespace) -> int:
    result = call_backend("DELETE", f"/sessions/{args.id}")
    if result is None:
        return 1
    print(f"{args.id}: {_DELETED.get(result['sync'], 'deleted here')}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="trading-r1 sessions", description="Your saved sessions.")
    sub = ap.add_subparsers(required=True)
    ls = sub.add_parser("list", help="the most recent sessions, with their sync mark")
    ls.add_argument("--limit", type=int, default=50, help="how many to show (default 50)")
    ls.set_defaults(handler=_list)
    rm = sub.add_parser("delete", help="delete a session here and its copy in your account")
    rm.add_argument("id", help="the session id (see `list`)")
    rm.set_defaults(handler=_delete)
    args = ap.parse_args(argv)
    return args.handler(args)
