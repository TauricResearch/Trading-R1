# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1 daemon`: keep the backend running in the background, under the user's own service
manager (a systemd user unit on Linux, a LaunchAgent on macOS; see ``host.daemon``).

Subcommands: install, uninstall, start, stop, restart, status, logs. Exit status 0 on success, 1
when the service manager is unavailable or a step fails.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from trading_r1.host.daemon import (
    ServiceError,
    SystemdUser,
    backend_report,
    install,
    keys_replaced,
    management_lock,
    manager_for,
    uninstall,
    unit_state,
)
from trading_r1.host.instance import default_port


def _no_user_bus() -> bool:
    """With no systemd user manager running for this user (``manager_for`` finds one that runs),
    ``systemctl --user`` fails with a bus error."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return not runtime or not (Path(runtime) / "bus").exists()


_DETAILS = """\
Service managers do not read your shell profile, so install copies what is set at that moment
and lists the names: API keys into the credentials store and TR1_* settings into
~/.trading-r1/daemon.env, both readable by you alone. Run it again after you change them or
update the program. Keys set in your shell replace stored ones. uninstall removes the service,
its settings and its log; keys stay in the store.

The service waits while a terminal's own backend is running and takes over when it ends. On
Linux, install turns on lingering, so the service starts at boot and survives logging out. On
macOS it runs while you are logged in."""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="trading-r1 daemon",
        description="Run the backend in the background.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_DETAILS,
    )
    ap.add_argument(
        "action", choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"]
    )
    ap.add_argument("--port", type=int, default=default_port())
    ap.add_argument("--no-env", action="store_true", help="install: copy no credentials")
    args = ap.parse_args(argv)
    try:
        manager = manager_for()
    except ServiceError as err:
        print(f"[trading-r1] {err}", file=sys.stderr)
        return 1
    if args.action == "status" and isinstance(manager, SystemdUser) and _no_user_bus():
        print("unit: no systemd user manager in this login")
        print(backend_report())
        return 0
    if args.action == "install" and isinstance(manager, SystemdUser) and _no_user_bus():
        manager.keep_running()  # lingering starts the user manager where a login did not
        manager = manager_for()
    if isinstance(manager, SystemdUser) and args.action != "logs" and _no_user_bus():
        print(
            "[trading-r1] no systemd user manager runs for you here. Run `trading-r1 serve` in a "
            "spare terminal instead.",
            file=sys.stderr,
        )
        return 1
    if args.action == "install":
        exe = shutil.which("trading-r1") or str(Path(sys.argv[0]).resolve())
        replaced = [] if args.no_env else keys_replaced(os.environ)
        try:
            with management_lock():
                copied = install(manager, exe, args.port, os.environ, copy_env=not args.no_env)
        except RuntimeError as err:
            print(f"[trading-r1] {err}", file=sys.stderr)
            return 1
        print(f"installed {manager.unit_path}")
        print("copied: " + (", ".join(copied) if copied else "no environment variables"))
        if replaced:
            print("replaced in the credentials store: " + ", ".join(replaced))
        print(manager.keep_running())
        return 0
    if args.action == "uninstall":
        with management_lock():
            uninstall(manager)
        print("removed; keys stay under `trading-r1 secrets`, and lingering is left as it was")
        return 0
    if args.action == "status":
        print(f"unit: {unit_state(manager)}")
        keeps = manager.keeps_running()
        if keeps is False:
            print("lingering is off: it stops when you log out (loginctl enable-linger $USER)")
        print(backend_report())
        return 0
    steps = manager.commands(args.action)
    for i, cmd in enumerate(steps):
        done = subprocess.run(cmd, check=False)
        if done.returncode != 0 and i == len(steps) - 1:
            return done.returncode
    return 0
