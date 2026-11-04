# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The `search` core tool: batched web discovery with pre-flight validation.

Runs one or more queries through a :class:`SearchEngine`, dedups by URL, records
titles, and (when a validator is provided) over-fetches candidates and keeps only
the fetchable ones: each is pre-fetched with a quick GET, so paywalls, bot-walls and
dead links are dropped before the model sees them, and the cache is warmed so the
later `visit` is a cache hit. Falls back to unvalidated results if validation yields none.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, hint
from trading_r1.contract.units import Code, Count, Text
from trading_r1.state.runtime_config import runtime_config
from trading_r1.tools.web.cache import FetchCache
from trading_r1.tools.web.search_engines import DdgsEngine, SearchEngine
from trading_r1.tools.web.visit_fetch import Fetcher

_DEFAULT_MAX_RESULTS = 8
_OVERFETCH_FACTOR = 2  # pull ~2× candidates so validation can drop the unfetchable
_MAX_VALIDATE = 16  # hard cap on pre-fetch validations per call (latency bound)
_MAX_QUERIES = 5  # a batch's most queries
_MAX_RESULTS = 20  # most results asked per query
_MIN_CHARS = 200  # extracted-text floor that distinguishes real content from a wall

_DESCRIPTION = (
    "Search the web for one or more queries and return ranked, fetchable results "
    "(title, URL, snippet) to investigate. Results are pre-checked for readability, "
    "so `visit` the ones you pick to read and cite them."
)


class SearchRequest(BaseModel):
    """Input for a batched web search."""

    queries: list[str] = Field(
        ..., max_length=_MAX_QUERIES, description="One or more search queries to run in this batch."
    )
    limit: int | None = Field(
        None, ge=1, le=_MAX_RESULTS, description="Optional cap on results per query (default 8)."
    )

    @field_validator("queries", mode="before")
    @classmethod
    def _wrap_single(cls, v: object) -> object:
        # A lone string is a common model shape for a list field: accept it.
        return [v] if isinstance(v, str) else v


class Hit(BaseModel):
    title: Code
    url: Code
    query: Code
    snippet: Text


class Validation(BaseModel):
    """The pre-flight check: candidates fetched, kept as readable, dropped."""

    candidates: Count
    validated: Count
    dropped: Count


class SearchOutput(Output):
    count: Count
    results: list[Hit]
    validation: Annotated[Validation | None, hint(hidden=True)] = None


async def _validate(candidates: list[dict], validator: Fetcher, cache: FetchCache) -> list[dict]:
    """Pre-fetch up to ``_MAX_VALIDATE`` candidates concurrently; keep the ones that
    return real content (warming the cache for an instant later visit)."""
    to_check = candidates[:_MAX_VALIDATE]
    sem = asyncio.Semaphore(runtime_config().fetch.validate_concurrency)

    async def check(c: dict) -> dict | None:
        async with sem:
            page = await validator(c["url"])
        if page.ok and len(page.text) >= _MIN_CHARS:
            cache.set_content(c["url"], page.text)  # warm: visit becomes a cache hit
            return c
        return None

    checked = await asyncio.gather(*(check(c) for c in to_check))
    return [c for c in checked if c is not None]


def build_search_spec(
    cache: FetchCache,
    engine: SearchEngine | None = None,
    *,
    default_max_results: int = _DEFAULT_MAX_RESULTS,
    validator: Fetcher | None = None,
) -> ToolSpec:
    """Build the per-run `search` tool bound to a cache + engine. When
    ``validator`` is given, results are pre-flight-validated (over-fetch, drop
    unfetchable, warm cache); without it, raw engine results are returned."""
    engine = engine or DdgsEngine()

    async def handler(req: SearchRequest) -> Result:
        per = req.limit or default_max_results
        cap = per * len(req.queries)
        fetch_n = per * _OVERFETCH_FACTOR if validator else per

        # Run the batch's queries concurrently (bounded): they're independent network
        # round-trips. Results are then merged in *query order* below, so dedup and ranking
        # are deterministic regardless of completion order; only the waiting overlaps.
        sem = asyncio.Semaphore(runtime_config().fetch.query_concurrency)

        async def run_query(q: str) -> list:
            async with sem:
                try:
                    return await engine.search(q, fetch_n)
                except Exception:  # noqa: BLE001  # one query failing shouldn't sink the batch
                    return []

        per_query = await asyncio.gather(*(run_query(q) for q in req.queries))

        seen: set[str] = set()
        candidates: list[dict] = []
        for q, hits in zip(req.queries, per_query, strict=True):
            for h in hits:
                if h.url in seen:
                    continue
                seen.add(h.url)
                cache.set_title(h.url, h.title)
                candidates.append(
                    {"title": h.title, "url": h.url, "snippet": h.snippet, "query": q}
                )

        if not candidates:
            raise ToolError(
                "No search results: the upstream search may be unavailable or the "
                "queries too narrow. Try rephrasing or fewer, broader queries."
            )

        validation = None
        if validator is not None:
            valid = await _validate(candidates, validator, cache)
            validation = Validation(
                candidates=len(candidates),
                validated=len(valid),
                dropped=len(candidates[:_MAX_VALIDATE]) - len(valid),
            )
            # Fall back to unvalidated candidates.
            results = (valid or candidates)[:cap]
        else:
            results = candidates[:cap]

        return Result(
            SearchOutput(count=len(results), results=results, validation=validation),
            Provenance(source=engine.name, subject="; ".join(req.queries)),
        )

    return ToolSpec(
        name="search",
        display_name="web.search",
        result_kind="web",
        tier="core",
        description=_DESCRIPTION,
        request_schema=SearchRequest,
        handler=handler,
        output=SearchOutput,
        tags=("web", "research"),
    )
