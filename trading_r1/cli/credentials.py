# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 secrets`: keep an API key, password or token where the application finds it.

The value is read from a prompt or from standard input, never from the command line, so it is not
left in a shell history or a process list. A key exported in the environment still wins over a
stored one.

Subcommands: set, list, delete. Exit status 0 on success, 1 on a failure.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from trading_r1.delivery.manage import credential_users
from trading_r1.providers.builtins import model_keys
from trading_r1.state import credentials


def _set(args: argparse.Namespace) -> int:
    if sys.stdin.isatty():
        value = getpass.getpass(f"{args.name} (input is hidden): ")
    else:
        value = sys.stdin.readline().rstrip("\r\n")
    try:
        credentials.set_value(args.name, value)
    except credentials.CredentialsError as err:
        print(err, file=sys.stderr)
        return 1
    print(f"{args.name} stored in {credentials.path()} (readable by you alone)")
    if users := credential_users(args.name):
        print(f"note: channel {', '.join(users)} uses this secret and now uses the new value")
    return 0


def _list(_args: argparse.Namespace) -> int:
    if (why := credentials.problem()) is not None:
        print(f"the store cannot be used: {why}", file=sys.stderr)
    try:
        stored = list(credentials.read_store())
    except credentials.CredentialsError:
        stored = []
    names = [*dict.fromkeys([*model_keys(), *stored])]
    for name, state in credentials.status(names).items():
        where = {"env": "environment", "store": "stored", None: "not set"}[state["source"]]
        note = " (the environment overrides the stored value)" if state.get("shadows_store") else ""
        print(f"{name}: {where}{note}")
    return 1 if why else 0


def _delete(args: argparse.Namespace) -> int:
    try:
        removed = credentials.delete(args.name)
    except credentials.CredentialsError as err:
        print(err, file=sys.stderr)
        return 1
    print(f"{args.name} {'removed' if removed else 'was not stored'}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="trading-r1 secrets", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    put = sub.add_parser("set", help="store a value (prompted, or read from standard input)")
    put.add_argument("name", help="e.g. ANTHROPIC_API_KEY")
    put.set_defaults(run=_set)
    sub.add_parser("list", help="which keys are set, and where from").set_defaults(run=_list)
    rm = sub.add_parser("delete", help="remove a stored value")
    rm.add_argument("name")
    rm.set_defaults(run=_delete)
    args = ap.parse_args(argv)
    return args.run(args)
