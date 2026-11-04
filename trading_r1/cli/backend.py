# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The backend this process serves, and `trading-r1 serve`, which runs it alone.

``start_backend`` takes the backend lock and serves the app on a thread, for ``serve`` and for a
terminal with no backend to attach to."""

from __future__ import annotations

import argparse
import os
import signal
import threading
import time

from trading_r1.cli.client import _err
from trading_r1.cli.logging_setup import configure_logging
from trading_r1.host import instance as host

_LOOPBACK = ("127.0.0.1", "localhost")
SHUTDOWN_S = 10  # what the server gives open requests when it stops
RECORDING_S = 20  # what a stopping backend gives a turn being recorded
WATCH_S = 2.0  # how often the backend looks at what it watches
START_POLL_S = 0.05  # how often a start looks for the server to be up
BACKEND_APP = "trading_r1.service.app:backend"  # a factory: the app with the scheduler running
DEV_APP = "trading_r1.service.app:app"  # the app alone, for the reloading dev server


class RunningBackend:
    """The backend this process serves: the server on its thread, the lock and the address."""

    def __init__(self, server, thread: threading.Thread, instance: host.Instance) -> None:  # noqa: ANN001
        self.server, self.thread, self.instance = server, thread, instance

    def stop(self) -> None:
        self.instance.withdraw()  # no new client attaches to a backend that is stopping
        self.server.should_exit = True
        self.thread.join(timeout=RECORDING_S)  # let a turn being recorded finish
        if not self.thread.is_alive():
            self.instance.release()  # else the lock goes when this process does


def start_backend(
    port: int, *, busy: str, wait: bool = False, log_config: dict | None = None
) -> RunningBackend | None:
    """Take the backend lock, serve the app on ``port`` on a thread and publish the address and a
    fresh token for the terminal and the CLI to find. None (after saying why) when this process
    cannot be the backend: another one holds the lock (``busy`` says so; with ``wait`` it is
    waited for), the state directory is not private, or the server did not start."""
    import uvicorn

    from trading_r1.state.paths import StateDirNotPrivateError

    instance = host.Instance()
    try:
        acquired = instance.acquire()
    except StateDirNotPrivateError as err:
        _err(f"[trading-r1] {err}")
        return None
    if not acquired and wait:
        _err("[trading-r1] another backend is running; waiting for it to end")
        while not acquired:
            time.sleep(WATCH_S)
            acquired = instance.acquire()
    if not acquired:
        _err(busy)
        return None
    token = os.environ.get(host.TOKEN_ENV) or host.new_token()
    os.environ[host.TOKEN_ENV] = token
    config = uvicorn.Config(
        BACKEND_APP,
        factory=True,
        host=_LOOPBACK[0],
        port=port,
        log_config=log_config,
        timeout_graceful_shutdown=SHUTDOWN_S,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    backend = RunningBackend(server, thread, instance)
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(START_POLL_S)
    if not server.started:
        _err("[trading-r1] backend failed to start.")
        backend.stop()
        return None
    from trading_r1 import __version__

    instance.publish(f"http://{_LOOPBACK[0]}:{port}", token, __version__)
    return backend


def main(argv: list[str]) -> int:
    """`trading-r1 serve`: run the HTTP+SSE service on the loopback address. It takes the
    backend lock (one backend writes the sessions) and publishes its address and a fresh token for
    the terminal and the CLI to find. `--reload` is the dev server: no lock, no token, no
    scheduler."""
    ap = argparse.ArgumentParser(prog="trading-r1 serve", description="Run the Trading-R1 service")
    ap.add_argument("--host", default=_LOOPBACK[0], help="the loopback address (the only one)")
    ap.add_argument("--port", type=int, default=host.default_port())
    ap.add_argument(
        "--reload",
        action="store_true",
        help="dev: auto-reload, no lock, no token check. Development only",
    )
    ap.add_argument(
        "--wait",
        action="store_true",
        help="when another backend holds the lock, wait for it to end instead of exiting",
    )
    args = ap.parse_args(argv)
    if args.host not in _LOOPBACK:
        ap.error("the service answers only on the loopback address (127.0.0.1)")
    import uvicorn

    configure_logging(to_file=False)  # server: logs on stderr, the usual place
    if args.reload:
        os.environ[host.AUTH_OFF_ENV] = "off"
        _err(f"[trading-r1] dev server on http://{args.host}:{args.port} (reload, no token)")
        uvicorn.run(DEV_APP, host=args.host, port=args.port, reload=True)
        return 0

    backend = start_backend(
        args.port,
        busy="[trading-r1] a backend is already running (trading-r1 daemon status)",
        wait=args.wait,
        log_config=uvicorn.config.LOGGING_CONFIG,
    )
    if backend is None:
        return 1
    _err(f"[trading-r1] serving on http://{args.host}:{args.port}")

    def terminated(signum: int, _frame: object) -> None:
        # end through the finally below, which releases the address and the lock
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminated)
    try:
        backend.thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        backend.stop()
    return 0
