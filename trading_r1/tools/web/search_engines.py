# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Search engines: a pluggable interface + the ddgs default.

The deep-research `search` tool talks to a engine through this small protocol.
The core ships `DdgsEngine` (the `ddgs` library, no infrastructure) and `StubEngine`
(canned results for offline tests).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    """One search hit returned by a engine."""

    title: str
    url: str
    snippet: str


class SearchEngine(Protocol):
    """Minimal async protocol implemented by search providers."""

    name: str

    async def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class DdgsEngine:
    """DuckDuckGo via the `ddgs` library. Synchronous under the hood, so the
    blocking call runs in a worker thread."""

    name = "ddgs"

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, max_results)

    @staticmethod
    def _search_sync(query: str, max_results: int) -> list[SearchResult]:
        from ddgs import DDGS

        rows = DDGS().text(query, max_results=max_results)
        out: list[SearchResult] = []
        for r in rows:
            url = r.get("href") or r.get("url") or ""
            if not url:
                continue
            out.append(
                SearchResult(
                    title=(r.get("title") or "").strip(),
                    url=url,
                    snippet=(r.get("body") or "").strip(),
                )
            )
        return out


@dataclass
class StubEngine:
    """Canned results for offline tests. ``by_query`` maps a query to results;
    a query with no entry returns ``default``."""

    name: str = "stub"
    by_query: dict[str, list[SearchResult]] | None = None
    default: list[SearchResult] | None = None

    async def search(self, query: str, max_results: int) -> list[SearchResult]:
        results = (self.by_query or {}).get(query, self.default or [])
        return results[:max_results]
