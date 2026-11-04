# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Who may call the service: a bearer token, and only from the machine itself.

Every route needs ``Authorization: Bearer <token>`` except ``GET /livez``. The token is
``TR1_API_TOKEN`` in the serving process's environment (``trading-r1 serve`` and the terminal's own
backend set it before the app starts, so a reload child inherits it). A request must also name a
loopback host and carry no ``Origin`` header: a web page's request to ``localhost`` has one, a
rebound DNS name has a foreign host, and ours have neither. ``TR1_API_AUTH=off`` switches all of
this off for the tests and ``make dev``.
"""

from __future__ import annotations

import hmac
import os

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from trading_r1.host.instance import AUTH_OFF_ENV, TOKEN_ENV

LIVENESS_PATH = "/livez"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def auth_enabled() -> bool:
    return os.environ.get(AUTH_OFF_ENV, "").lower() != "off"


def _host_of(header: str) -> str:
    """The host in a ``Host`` header, without its port."""
    host = header.strip().lower()
    if host.startswith("["):  # [::1]:8080
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


class AuthMiddleware:
    """Pure ASGI, so a streamed response is never buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not auth_enabled():
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = self._problem(scope["path"], headers)
        if problem is None:
            await self.app(scope, receive, send)
            return
        status, message = problem
        await JSONResponse({"detail": message}, status_code=status)(scope, receive, send)

    @staticmethod
    def _problem(path: str, headers: dict[str, str]) -> tuple[int, str] | None:
        if _host_of(headers.get("host", "")) not in _LOOPBACK_HOSTS:
            return 403, "this service answers only on the loopback address"
        if "origin" in headers:
            return 403, "browser requests are not accepted"
        if path == LIVENESS_PATH:
            return None
        expected = os.environ.get(TOKEN_ENV, "")
        if not expected:
            return 503, f"no API token is configured ({TOKEN_ENV})"
        scheme, _, given = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip(), expected):
            return 401, "a valid bearer token is required"
        return None
