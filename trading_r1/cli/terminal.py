# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The interactive terminal: the Bun TUI in the foreground, on a running backend or its own."""

from __future__ import annotations

import os
import shutil
import signal
from pathlib import Path

from trading_r1.cli.backend import _LOOPBACK, RunningBackend, start_backend
from trading_r1.cli.client import _err
from trading_r1.cli.logging_setup import configure_logging
from trading_r1.host import instance as host

_TERMINAL_GRACE_S = 5.0


def _free_port(host: str, port: int) -> int:
    """Return ``port`` if it's free to bind, else an OS-assigned free port on ``host``, so a
    foreign process on the default port doesn't get hijacked or block startup."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return port
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _find_tui_dir(_pkg_dir: Path | None = None) -> Path | None:
    """Locate the Bun TUI source in both layouts: an installed wheel bundles it at
    ``trading_r1/_tui/``, a dev checkout keeps it at the repo-root
    ``tui/``. Returns the first that has a ``package.json``, or None if neither does.
    ``_pkg_dir`` overrides the package directory (for tests)."""
    pkg = _pkg_dir or Path(__file__).resolve().parent.parent
    for cand in (pkg / "_tui", pkg.parent / "tui"):
        if (cand / "package.json").is_file():
            return cand
    return None


def _bun_found() -> bool:
    """Whether `bun` can run, from PATH or where its installer puts it: a shell opened before the
    install does not have that on PATH yet."""
    if shutil.which("bun"):
        return True
    bin_dir = Path(os.environ.get("BUN_INSTALL", "~/.bun")).expanduser() / "bin"
    if not (bin_dir / "bun").is_file():
        return False
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    return True


def _ensure_tui_deps(tui_dir: Path) -> bool:
    """Install the TUI's Bun dependencies when they're missing or stale, and return whether
    they're ready.

    A fresh checkout or install has no ``node_modules`` (it's git-ignored, not in the wheel);
    launching ``bun`` without it mis-resolves peer deps and the UI dies. After a successful
    ``bun install``, ``node_modules/.deps-stamp`` gets a hash of ``bun.lock``. The tree is ready
    only when that stamp matches the current lock, so a fresh install installs, and an
    interrupted one (no stamp) and an upgrade (``uv tool install`` ships a new ``bun.lock`` but
    leaves the old ``node_modules``) install again. A matching stamp is a no-op. ``bun install``
    resolves per package.json's ranges, so nothing is pinned."""
    import contextlib
    import hashlib
    import subprocess

    node_modules = tui_dir / "node_modules"
    lock = tui_dir / "bun.lock"
    marker = node_modules / ".deps-stamp"

    def _lock_sig() -> str:
        try:
            return hashlib.sha256(lock.read_bytes()).hexdigest()
        except OSError:
            return ""

    try:
        if node_modules.is_dir() and marker.read_text().strip() == _lock_sig():
            return True  # installed against the current lockfile: nothing to do
    except OSError:
        pass  # no marker (fresh or interrupted install): install below
    _err("[trading-r1] installing TUI dependencies…")
    try:
        code = subprocess.run(["bun", "install"], cwd=str(tui_dir)).returncode
    except OSError as e:  # bun vanished between the which() check and here
        _err(f"[trading-r1] bun install failed: {e}")
        return False
    if code != 0:
        _err("[trading-r1] bun install failed; see the output above.")
        return False
    # stamp the lock just installed against; a missing stamp reinstalls next time
    with contextlib.suppress(OSError):
        marker.write_text(_lock_sig())
    return True


def _tui(
    initial_prompt: str | None = None,
    profile: str | None = None,
    mode: str | None = None,
) -> int:
    """Launch the full app as one command: the backend (in-process, unless one is
    already running) + the TUI in the foreground, tearing the backend down on exit.

    A running backend (the daemon, or another terminal's) is attached to through the address it
    published; if none runs, this process serves one on a thread, holding the backend lock, and
    stops it on exit. The TUI is a Bun child that owns the terminal and gets the address and token.

    ``--profile`` and ``--mode`` are honored by handing them to the TUI subprocess as
    ``TR1_PROFILE`` / ``TR1_MODE``: the TUI then sends that profile on every ``/research`` and
    ``/health`` call and seeds its initial execution mode from the mode, so they also apply on a
    backend that is already running."""

    # The Bun UI owns the terminal while the backend runs in-process, so backend logs go to
    # a file: a stray stderr line would corrupt the display.
    configure_logging(to_file=True)

    api_url = os.environ.get("TR1_API_URL")  # an explicit address is attached to, never started
    token = os.environ.get(host.TOKEN_ENV, "")
    backend: RunningBackend | None = None

    def stop_backend() -> None:
        if backend is not None:
            backend.stop()

    mismatch = ""
    if api_url is None:
        from trading_r1 import __version__

        try:
            running = host.find_backend()
        except host.ServiceUnreachableError as err:
            _err(f"[trading-r1] {err}")
            return 1
        if running is not None:
            api_url, token = running.url, running.token
            if running.version != __version__:
                mismatch = (
                    f"The running backend is version {running.version}, this is {__version__}: "
                    "restart it (trading-r1 daemon restart)"
                )
        else:
            port = _free_port(_LOOPBACK[0], host.default_port())
            _err("[trading-r1] starting backend…")
            backend = start_backend(
                port,
                busy="[trading-r1] a backend is starting; run trading-r1 again in a moment.",
                # Don't let uvicorn install its own stderr handlers over ours:
                # configure_logging already routed uvicorn's loggers to the file so the TUI
                # terminal stays clean.
                log_config=None,
            )
            if backend is None:
                return 1
            api_url, token = f"http://{_LOOPBACK[0]}:{port}", os.environ[host.TOKEN_ENV]

    tui_dir = _find_tui_dir()
    if tui_dir is None or not _bun_found():
        _err(
            "[trading-r1] the terminal needs `bun`. Install it from https://bun.sh, then try again."
        )
        stop_backend()
        return 1

    if not _ensure_tui_deps(tui_dir):  # fresh checkout: install bun deps before launching
        stop_backend()
        return 1

    env = {**os.environ, "TR1_API_URL": api_url, host.TOKEN_ENV: token}
    if mismatch:
        env["TR1_LAUNCH_NOTICE"] = mismatch
    if initial_prompt:
        env["TR1_INITIAL_PROMPT"] = initial_prompt
    if profile:
        env["TR1_PROFILE"] = profile  # the TUI sends this on every /research + /health
    if mode:
        env["TR1_MODE"] = mode  # the TUI seeds its initial execution mode from this
    try:
        # --conditions=browser forces solid-js to its client build. Bun's default (node)
        # condition resolves solid-js to its SSR build (isServer=true), whose reactivity is
        # stubbed: signal writes then crash and the UI can't update. The TUI is fully
        # interactive, so it needs the client reactive build.
        cmd = ["bun", "--conditions=browser", "src/index.tsx"]
        return _run_terminal(cmd, str(tui_dir), env)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_backend()


def _run_terminal(cmd: list[str], cwd: str, env: dict[str, str]) -> int:
    """Run the terminal to its end. Told to end (SIGTERM, SIGHUP), it is told too and given a
    moment to restore the screen, then this process ends through its caller's cleanup."""
    import subprocess
    import threading

    child: subprocess.Popen[bytes] | None = None

    def ended(signum: int, _frame: object) -> None:
        if child is None:  # not started yet: end here
            raise SystemExit(128 + signum)
        child.terminate()
        timer = threading.Timer(_TERMINAL_GRACE_S, child.kill)
        timer.daemon = True
        timer.start()

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, ended)
    try:
        child = subprocess.Popen(cmd, cwd=cwd, env=env)
        return child.wait()
    except BaseException:
        if child is not None:
            child.kill()
            child.wait()
        raise
