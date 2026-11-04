# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The one backend of a user: the lock it holds and the address it publishes.

Sessions, settings and scheduled jobs have one writer: whichever process serves, the
daemon or the terminal's own backend, holds an exclusive lock on ``backend.lock`` for as long as
it lives (the OS drops it if the process dies) and, once it is listening, writes ``backend.json``
(its address, token and pid, readable by the user alone). A client finds the backend by that
file; it starts a backend of its own only when the lock is free.

State lives in ``$TR1_HOME`` (default ``~/.trading-r1``).
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import secrets
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from trading_r1.state.fileio import MISSING, PrivateFileError, read_private_json, write_json
from trading_r1.state.paths import private_state_dir, state_dir

LOCK_FILE = "backend.lock"
VERIFY_S = 2.0  # a loopback answer is immediate
FIND_WAIT_S = 10.0
FIND_POLL_S = 0.3
META_FILE = "backend.json"
TOKEN_ENV = "TR1_API_TOKEN"  # the serving process's token; the app reads it from here
AUTH_OFF_ENV = "TR1_API_AUTH"  # "off" switches the token check off (tests, `make dev`)
DEFAULT_PORT = 8765


def default_port() -> int:
    """The port the backend asks for: ``TR1_PORT`` or the default."""
    try:
        port = int(os.environ.get("TR1_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Backend:
    """A running backend as published: where to reach it and the token it wants."""

    url: str
    token: str
    pid: int
    version: str


class Instance:
    """The lock and the published address of the backend this process is."""

    def __init__(self) -> None:
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Take the lock without waiting; False when another backend holds it."""
        directory = private_state_dir()
        fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            os.close(fd)
            if err.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise  # not "someone holds it": the lock cannot be used at all
            return False
        self._fd = fd
        return True

    def publish(self, url: str, token: str, version: str) -> None:
        """Record the address once the server is listening."""
        meta = {"url": url, "token": token, "pid": os.getpid(), "version": version}
        write_json(state_dir() / META_FILE, meta, private=True)

    def withdraw(self) -> None:
        """Stop advertising the address (the backend is shutting down)."""
        with contextlib.suppress(OSError):
            (state_dir() / META_FILE).unlink()

    def release(self) -> None:
        """Drop the address, then the lock. The lock file stays: removing it would let a second
        process lock a different file of the same name."""
        if self._fd is None:
            return
        self.withdraw()
        os.close(self._fd)  # closing the descriptor releases the lock
        self._fd = None


def lock_held() -> bool:
    """Whether some backend holds the lock now."""
    path = state_dir() / LOCK_FILE
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def _loopback_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}


def read_published() -> Backend | None:
    """The address a backend published, or None if there is none, it is not the user's alone to
    read, or it names something other than a loopback address."""
    try:
        data = read_private_json(state_dir() / META_FILE)
        if data is MISSING:
            return None
        backend = Backend(
            str(data["url"]), str(data["token"]), int(data["pid"]), str(data.get("version", ""))
        )
    except (PrivateFileError, ValueError, KeyError, TypeError):
        return None
    return backend if _loopback_url(backend.url) and backend.token else None


def verify(backend: Backend, *, timeout: float = VERIFY_S) -> bool:
    """Whether the process at the published address is that backend: it accepts the token and
    reports the published pid. The token goes nowhere but the published loopback address, and a
    redirect is not followed."""
    request = urllib.request.Request(
        f"{backend.url.rstrip('/')}/version", headers={"Authorization": f"Bearer {backend.token}"}
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    try:
        # no proxy: the token goes to the loopback address and nowhere else
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
        with opener.open(request, timeout=timeout) as resp:
            body = json.load(resp)
    except Exception:  # noqa: BLE001 (unreachable, refused or not ours)
        return False
    return body.get("service") == "trading-r1" and body.get("pid") == backend.pid


def find_backend(*, wait_s: float = FIND_WAIT_S) -> Backend | None:
    """The running backend, if one holds the lock: its published address once it verifies. A
    backend still starting is waited for up to ``wait_s``; None when none runs, or when one holds
    the lock but cannot be reached within the wait (the caller must not start a second)."""
    deadline = time.monotonic() + wait_s
    while True:
        if not lock_held():
            return None
        backend = read_published()
        if backend is not None and verify(backend):
            return backend
        if time.monotonic() >= deadline:
            raise ServiceUnreachableError(
                "a Trading-R1 backend holds the lock but does not answer; stop it "
                "(`trading-r1 daemon stop`) or point TR1_HOME elsewhere"
            )
        time.sleep(FIND_POLL_S)


class ServiceUnreachableError(RuntimeError):
    """The Trading-R1 service holds the lock but cannot be reached."""
