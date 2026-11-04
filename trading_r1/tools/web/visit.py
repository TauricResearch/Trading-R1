# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The `visit` core tool: retrieve pages and extract their content.

For each URL: fetch (HTML via trafilatura, PDF via pdfplumber), then run a
reader-model extraction over the page text returning ``{accessible, summary,
evidence}``. Only accessible, non-empty pages are stored and added to the
citation index; paywalls, bot-walls and empty pages are reported but never cited.

Fetch and the reader client are injectable so the tool is unit-testable offline.
The reader runs through the same provider seam using the paired reader profile
selected for the active backbone.
"""

from __future__ import annotations

import asyncio
import json
import re

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from trading_r1.contract.spec import Output, Result, ToolSpec, rendered
from trading_r1.contract.units import Code, Count, Text
from trading_r1.providers import ModelProfile
from trading_r1.runtime.sources import cite_source
from trading_r1.runtime.tool_events import emit_tool_progress
from trading_r1.state.runtime_config import RuntimeConfig, load_runtime_config
from trading_r1.tools.web.cache import FetchCache
from trading_r1.tools.web.visit_fetch import Fetcher, http_fetch
from trading_r1.tools.web.visit_reader import read_page

__all__ = ["VisitRequest", "_coerce_urls", "build_visit_spec"]

_MAX_URLS = 8  # most pages one call reads
_HEADLINE_CHARS = 140  # the rail wraps, but cap so one source can't dominate

_DESCRIPTION = (
    "Fetch one or more web pages and return summaries with "
    "citation markers. Accessible pages are added to the bibliography; cite them "
    "inline as [N]. Use after `search` to read the most relevant results; pass the "
    "several you want to read in one call; they're fetched and read in parallel."
)


def _split_one(s: str) -> list[str]:
    """Split one string into URLs: a JSON array, or a comma/whitespace-joined run."""
    s = s.strip()
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (ValueError, TypeError):
            pass
    if "," in s or "\n" in s:
        urls = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip().startswith("http")]
        if urls:
            return urls
    return [s]


def _coerce_urls(v: object) -> object:
    """Repair the URL-list shapes models emit: a lone string, a
    JSON-stringified array, or a comma / whitespace-joined string (also inside a
    list element)."""
    if isinstance(v, str):
        return _split_one(v)
    if isinstance(v, list):
        out: list[str] = []
        for item in v:
            out.extend(_split_one(item) if isinstance(item, str) else [item])
        return out
    return v


class VisitRequest(BaseModel):
    """Input for fetching and reading one or more URLs."""

    urls: list[str] = Field(
        ..., max_length=_MAX_URLS, description="One or more page URLs to fetch and read."
    )
    goal: str | None = Field(None, description="Optional focus for the extraction.")

    @field_validator("urls", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> object:
        return _coerce_urls(v)


def _headline(summary: str) -> str:
    """A one-line takeaway from the reader summary for the live sources rail:
    the first sentence if it's a sensible length, else a truncation."""
    s = " ".join(summary.split())  # collapse newlines/runs
    m = re.match(r"(.+?[.!?])(\s|$)", s)
    head = m.group(1) if m and len(m.group(1)) <= _HEADLINE_CHARS else s
    return head if len(head) <= _HEADLINE_CHARS else head[: _HEADLINE_CHARS - 1].rstrip() + "…"


class Page(BaseModel):
    """One URL as read: a page that was read carries its bibliography number, the reader's
    summary and a verbatim excerpt; one that was not says why (``error``, or
    ``reader_error`` when the reader itself failed on a fetched page)."""

    url: Code
    accessible: bool
    citation_index: Count = None
    summary: Text = None
    evidence: Text = None
    error: Text = None
    reader_error: Text = None


class VisitOutput(Output):
    model_config = rendered(
        then="Cite a page that was read inline as [citation_index]. A page not accessible is "
        "unread, not evidence: rely on another source or note the gap. A reader_error is a "
        "tool or configuration failure, not a paywall."
    )

    cited: Count
    pages: list[Page]


def build_visit_spec(
    cache: FetchCache,
    profile: ModelProfile,
    *,
    fetcher: Fetcher | None = None,
    client: AsyncOpenAI | None = None,
    config: RuntimeConfig | None = None,
) -> ToolSpec:
    """Build the per-run `visit` tool bound to the cache + reader backbone."""
    fetcher = fetcher or http_fetch
    cfg = config or load_runtime_config()

    async def handler(req: VisitRequest) -> Result:
        total = len(req.urls)
        # Fetch and read the pages concurrently (bounded). Citation indices and `source` events
        # are assigned in a sequential pass afterwards, in the request's URL order, so the
        # bibliography [N] is deterministic no matter which read finishes first.
        sem = asyncio.Semaphore(max(1, cfg.reader.max_concurrency))

        async def load_and_read(i: int, url: str) -> dict:
            tag = f"({i + 1}/{total}) " if total > 1 else ""
            short = url.split("://")[-1][:42]
            # Each task is self-contained: a page's failure becomes a failed-page entry, never
            # a raised exception. So `gather` can't propagate (which would leave the sibling
            # tasks orphaned, still fetching, mutating the cache, and emitting into a
            # now-closed event scope). fetch/read already normalize their own errors; this
            # guard also covers an injected fetcher or emitter that raises.
            try:
                async with sem:
                    if cache.has_content(url):
                        text = cache.get_content(url) or ""
                    else:
                        emit_tool_progress(f"{tag}fetching {short}")
                        page = await fetcher(url)
                        if not page.ok:
                            return {"url": url, "fetch_ok": False, "error": page.error}
                        text = page.text
                        cache.set_content(url, text)
                    emit_tool_progress(f"{tag}reading {short}")
                    reader = await read_page(
                        profile, text, goal=req.goal, client=client, config=cfg
                    )
                return {"url": url, "fetch_ok": True, "reader": reader}
            except Exception as exc:  # noqa: BLE001  # a page's failure is reported, not raised
                return {"url": url, "fetch_ok": False, "error": f"{type(exc).__name__}: {exc}"}

        loaded = await asyncio.gather(*(load_and_read(i, url) for i, url in enumerate(req.urls)))

        pages: list[dict] = []
        for entry in loaded:  # URL order gives deterministic citation indices
            url = entry["url"]
            if not entry["fetch_ok"]:
                pages.append(Page(url=url, accessible=False, error=entry["error"]))
                continue
            reader = entry["reader"]
            if reader["accessible"] and reader["summary"]:
                idx = cite_source(url, cache.get_title(url), _headline(reader["summary"]), cache)
                pages.append(
                    Page(
                        url=url,
                        accessible=True,
                        citation_index=idx,
                        summary=reader["summary"],
                        evidence=reader.get("evidence") or None,
                    )
                )
            else:
                error = reader.get("error")
                if not reader.get("reader_error") and not error:
                    error = "not accessible (paywall / bot-wall / empty)"
                pages.append(
                    Page(
                        url=url,
                        accessible=False,
                        summary=reader.get("summary") or None,
                        error=error,
                        reader_error=reader.get("reader_error"),
                    )
                )
        return Result(VisitOutput(cited=sum(p.accessible for p in pages), pages=pages))

    return ToolSpec(
        name="visit",
        display_name="web.visit",
        result_kind="web",
        tier="core",
        description=_DESCRIPTION,
        request_schema=VisitRequest,
        handler=handler,
        output=VisitOutput,
        tags=("web", "research"),
    )
