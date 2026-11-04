# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Calls from the command line to the running backend, with its published token.

``call_backend`` returns the decoded answer, or None after printing why to standard error, so a
subcommand turns None into exit status 1."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from trading_r1.host.instance import ServiceUnreachableError, find_backend

REQUEST_S = 120  # a call to the local service; a whole research run streams past this


def _err(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _readable(detail: object) -> object:
    """A validation error's list of problems as a line; anything else as it came."""
    if not isinstance(detail, list):
        return detail
    parts = []
    for problem in detail:
        where = ".".join(str(p) for p in problem.get("loc", [])[1:])
        parts.append(f"{where}: {problem.get('msg', '')}" if where else problem.get("msg", ""))
    return "; ".join(parts)


def call_backend(method: str, path: str, body: dict | None = None) -> dict | None:
    """One authenticated call to the running backend, or None (and a message) when none runs."""
    try:
        backend = find_backend()
    except ServiceUnreachableError as err:
        _err(f"[trading-r1] {err}")
        return None
    if backend is None:
        _err(
            "[trading-r1] no backend is running: start one with `trading-r1 serve`, or keep one "
            "running with `trading-r1 daemon install`."
        )
        return None
    request = urllib.request.Request(
        backend.url.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {backend.token}", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=REQUEST_S) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        try:
            detail = _readable(json.load(err).get("detail", ""))
        except ValueError:
            detail = ""
        _err(
            f"[trading-r1] {detail}" if detail else f"[trading-r1] the backend answered {err.code}"
        )
    except OSError as err:
        _err(f"[trading-r1] the backend did not answer: {err}")
    return None
