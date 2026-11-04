# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Answers the Tauric API allows to be reused: the response's own ``Cache-Control``.

A successful read that states ``max-age=N`` is kept for N seconds and answered locally, with no
request, until then. Nothing here sets a lifetime of its own: a response without the header, an
error, ``no-store`` / ``no-cache`` or ``max-age=0`` is not kept. Entries are keyed by the
credential (a hash), the base URL, the path and the sorted query, so one key's answers are never
served to another and a different query is a different entry.
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections import OrderedDict
from typing import Any

import httpx

_MAX_ENTRIES = 256
Key = tuple[str, str, str, str]


def lifetime(cache_control: str | None, age: str | None = None) -> int:
    """Seconds a response may still be reused for, less the time it has already spent in a
    cache (``Age``); 0 when it states no ``max-age``, states two, or forbids reuse."""
    seconds: list[int] = []
    for directive in (cache_control or "").split(","):
        name, _, value = directive.strip().partition("=")
        name = name.strip().lower()
        if name in ("no-store", "no-cache"):
            return 0
        if name == "max-age":
            value = value.strip().strip('"')
            if not value.isdigit():
                return 0
            seconds.append(int(value))
    if len(seconds) != 1:
        return 0
    spent = int(age) if age and age.strip().isdigit() else 0
    return max(0, seconds[0] - spent)


def key_for(base: str, secret: str, path: str, query: dict[str, Any]) -> Key:
    """The credential's hash, the base, the path and the query exactly as it is sent."""
    who = hashlib.sha256(secret.encode()).hexdigest()[:16]
    return (who, base, path, str(httpx.QueryParams(sorted(query.items()))))


class Reuse:
    """A bounded in-memory store of reusable answers, oldest evicted first."""

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._max = max_entries
        self._items: OrderedDict[Key, tuple[float, Any]] = OrderedDict()

    def get(self, key: Key) -> Any | None:
        """A copy of the stored body while it is still allowed, else None."""
        item = self._items.get(key)
        if item is None or item[0] <= time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return copy.deepcopy(item[1])

    def put(self, key: Key, body: Any, seconds: int) -> None:
        if seconds <= 0:
            return
        self._items[key] = (time.monotonic() + seconds, copy.deepcopy(body))
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


REUSE = Reuse()
