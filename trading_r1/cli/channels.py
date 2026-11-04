# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 channels`: set up where reports are sent, and test it.

What each kind of channel asks for comes from its declaration, so the options below are
generated from it. Settings go in ``~/.trading-r1/channels.json``; a password or token is kept in
the environment or the credentials store (``trading-r1 secrets``) and the file holds only its name.
A test send goes through the running backend, which owns the outbox.

Subcommands: add KIND, list, kinds, remove NAME, test NAME. Exit status 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from trading_r1.cli.client import _err, call_backend
from trading_r1.delivery.base import ChannelError, Kind
from trading_r1.delivery.channels import build_channels
from trading_r1.delivery.config import ConfigError, read_config
from trading_r1.delivery.kinds import KINDS
from trading_r1.delivery.manage import refuse_model_key, remove_channel, save_channel
from trading_r1.delivery.telegram import TelegramError, find_private_chat
from trading_r1.state import credentials

WAIT_S = 120.0


def print_outcomes(result: dict) -> int:
    """Show what each channel did; the exit code is 0 only when all of them sent."""
    code = 0
    for o in result["outcomes"]:
        detail = f": {o['detail']}" if o.get("detail") else ""
        print(f"{o['channel']}: {o['status']} ({o['address']}){detail}")
        if o["status"] != "sent":
            code = 1
    return code


def _flag(field_name: str) -> str:
    return "--" + field_name.replace("_", "-")


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{question} [y/N] ").strip().lower() == "y"


def _find_chat(token: str, assume_yes: bool) -> int | None:
    """The owner's private chat with their bot, once they confirm it is them."""
    print(f"Send any message to your bot in Telegram now (waiting up to {int(WAIT_S)} s)...")
    try:
        chat = asyncio.run(find_private_chat(token, WAIT_S))
    except TelegramError as exc:
        _err(str(exc))
        return None
    if chat is None:
        _err("no message arrived")
        return None
    who = chat.get("username") or chat.get("first_name") or "this chat"
    if not _confirm(f"Send reports to {who} (chat {chat['id']})? That must be you.", assume_yes):
        return None
    return int(chat["id"])


def _collect(kind: Kind, args: argparse.Namespace) -> dict:
    raw = {**kind.presets.get(getattr(args, "preset", None) or "", {})}
    for f in kind.fields:
        value = getattr(args, f.name, None)
        if value is not None:
            raw[f.name] = value
    return raw


def _add(args: argparse.Namespace) -> int:
    kind = KINDS[args.kind]
    name = args.name or kind.name
    raw = _collect(kind, args)
    try:
        # the settings as far as they go: a Telegram chat is found once its token is known
        values = kind.validate({**raw, "chat_id": 1} if kind.name == "telegram" else raw)
        secrets: dict[str, str] = {}
        for f in kind.secret_fields():
            credential = values.get(f.name)
            if credential:
                refuse_model_key(f.name, credential)
                missing = not credentials.status([credential])[credential]["configured"]
                if missing and (value := _ask_secret(credential, f.label)):
                    secrets[f.name] = value
        if kind.name == "telegram" and "chat_id" not in raw:
            token = secrets.get("token_env") or credentials.overlay().get(values["token_env"], "")
            if not token:
                _err(
                    f"set {values['token_env']} to your bot's token first: trading-r1 secrets set …"
                )
                return 1
            chat_id = _find_chat(token, args.yes)
            if chat_id is None:
                return 1
            values["chat_id"] = chat_id
        save_channel(name, kind.name, values, secrets)
    except (ChannelError, ConfigError, credentials.CredentialsError) as err:
        _err(str(err))
        return 1
    print(f"saved. Try it: trading-r1 channels test {name}")
    return 0


def _ask_secret(name: str, label: str) -> str | None:
    """Ask for a missing password or token (hidden), or say how to set it."""
    if not sys.stdin.isatty():
        print(f"{label} is not set: export {name}=... or run: trading-r1 secrets set {name}")
        return None
    value = getpass.getpass(f"{label} ({name}, input is hidden; empty to set it later): ")
    if not value:
        print(f"Set it later: export {name}=... or: trading-r1 secrets set {name}")
        return None
    return value


def _list(_args: argparse.Namespace) -> int:
    try:
        config = read_config()
    except ConfigError as err:
        _err(str(err))
        return 1
    channels, problems = build_channels()
    if not config:
        print("no channels yet: trading-r1 channels add email|telegram|folder")
    for name, cfg in config.items():
        if name in channels:
            print(f"{name} ({cfg.get('kind', name)}): {channels[name].address()}")
        else:
            print(f"{name} ({cfg.get('kind', name)}): not usable: {problems.get(name, '?')}")
    return 0


def _kinds(_args: argparse.Namespace) -> int:
    for k in KINDS.values():
        presets = f" (presets: {', '.join(k.presets)})" if k.presets else ""
        print(f"{k.name}: {k.summary}{presets}")
    return 0


def _remove(args: argparse.Namespace) -> int:
    try:
        users = remove_channel(args.name, force=args.force)
    except (ChannelError, ConfigError) as err:
        hint = " (--force to remove it anyway)" if "scheduled jobs" in str(err) else ""
        _err(f"{err}{hint}")
        return 1
    print(f"removed {args.name}" + (f"; jobs that listed it: {', '.join(users)}" if users else ""))
    return 0


_DETAILS = """\
Passwords and tokens are looked up by name in the environment, then in the credentials store;
channels.json holds only the names. Each send ends as sent, failed, or unknown (the request went
out and no answer came back; it is not retried on its own).

Gmail: turn on 2-Step Verification and make an app password in your Google account. Microsoft
365 has no preset, because Microsoft is retiring password sign-in for SMTP; where your admin
still allows it, give --host smtp.office365.com."""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="trading-r1 channels",
        description="Where reports are sent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DETAILS,
    )
    sub = ap.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="set up a channel")
    kinds = add.add_subparsers(dest="kind", required=True)
    for kind in KINDS.values():
        p = kinds.add_parser(kind.name, help=kind.summary)
        p.add_argument("--name", help=f"what a job calls it (default: {kind.name})")
        p.add_argument("--yes", action="store_true", help="do not ask to confirm the chat")
        if kind.presets:
            p.add_argument("--preset", choices=sorted(kind.presets), help="prefill the server")
        for f in kind.fields:
            options = {"dest": f.name, "default": None, "help": f.help or f.label}
            if f.type == "int":
                options["type"] = int
            elif f.type == "choice":
                options["choices"] = list(f.choices)
            elif f.type == "list":
                options["nargs"] = "+"
            p.add_argument(_flag(f.name), **options)
    sub.add_parser("list", help="the configured channels and whether each is usable")
    sub.add_parser("kinds", help="the kinds of channel")
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    rm.add_argument("--force", action="store_true")
    sub.add_parser("test", help="send a test message").add_argument("name")
    args = ap.parse_args(argv)
    if args.action == "add":
        return _add(args)
    if args.action == "list":
        return _list(args)
    if args.action == "kinds":
        return _kinds(args)
    if args.action == "remove":
        return _remove(args)
    result = call_backend("POST", f"/channels/{args.name}/test")
    return 1 if result is None else print_outcomes(result)
