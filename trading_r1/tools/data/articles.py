# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Tauric news articles: one shape for every tool that shows them; resolving the article ids
that digests and theses cite into links; and adding the articles to the run's numbered sources,
so the answer cites them as `[N]` like the pages `visit` reads."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Annotated, Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import ToolError, hint
from trading_r1.contract.units import Code, Count, Day, Text, Unit
from trading_r1.runtime.sources import cite_source, in_run
from trading_r1.tools.data.tauric import NOT_PUBLISHED, Tauric

_SUMMARY_CHARS = 280  # the link carries the article
VERIFY = (
    "Machine-generated from the articles Tauric collects: verify a claim through its sources, "
    "and open them with `visit`."
)
# A backstop on cited articles resolved per call; past it,
# each claim keeps its first sources before any claim gets a second.
MAX_SOURCES = 80
NEWS_PAGE = 25  # Tauric's most news items per response


class Article(BaseModel):
    """An article as the tools show it: the run's source number `[N]` (its Tauric id outside a
    run), time and its basis (published, or first seen by Tauric), publisher and the
    publishers of syndicated copies (or their count), headline, Tauric's read, link and summary."""

    # what claims cite it by: the run's [N], or its Tauric id
    source: Annotated[int | str | None, Field(json_schema_extra={"unit": Unit.CODE.value})] = None
    id: Annotated[str | None, hint(hidden=True)] = None
    published: Day = None
    time_basis: Code = None
    publisher: Code = None
    also_in: list[Code] = []
    copies: Count = None  # articles in the story, when Tauric folds several into one row
    title: Code
    sentiment: Code = None
    impact: Code = None
    event_types: list[Code] = []
    url: Code = None
    summary: Text = None


class Cited(Article):
    """An article a digest's claims cite: the claims say what it reports, so its own summary
    and Tauric's tags stay in the payload, and the sources read as one table."""

    event_types: Annotated[list[Code], hint(hidden=True)] = []
    summary: Annotated[Text, hint(hidden=True)] = None


def article(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One Tauric article in our shape, or None without a title."""
    title = raw.get("title")
    if not title:
        return None
    e = raw.get("annotations") or {}
    summary = " ".join(" ".join(e.get("summary") or []).split())
    if len(summary) > _SUMMARY_CHARS:
        summary = summary[: _SUMMARY_CHARS - 1].rstrip() + "…"
    return {
        "id": raw.get("id"),
        "title": title,
        "publisher": raw.get("publisher"),
        "published": raw.get("article_time"),
        "time_basis": raw.get("article_time_kind"),
        "url": raw.get("url"),
        "summary": summary or None,
        "sentiment": e.get("sentiment"),
        "impact": e.get("impact"),
        "event_types": e.get("event_types") or [],
    }


def collapse(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One item per story: syndicated copies (the same title from several publishers) fold
    into the first listed, which names the other publishers in `also_in`. Order is kept."""
    first: dict[str, dict[str, Any]] = {}
    for item in items:
        key = " ".join(item["title"].lower().split())
        if key not in first:
            first[key] = item
            continue
        kept, other = first[key], item["publisher"]
        also = kept.setdefault("also_in", [])
        if other and other != kept["publisher"] and other not in also:
            also.append(other)
    for item in first.values():
        if not item.get("also_in"):
            item.pop("also_in", None)
    return list(first.values())


def cited_ids(*blocks: Any) -> list[str]:
    """Every cited article id under the given content, without repeats, ordered so each claim's
    first source comes before any claim's second (a cap then keeps every claim sourced)."""
    claims: list[list[str]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if x.get("article_ids"):
                claims.append(list(x["article_ids"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for b in blocks:
        walk(b)
    seen: dict[str, None] = {}
    for rank in range(max((len(c) for c in claims), default=0)):
        for c in claims:
            if rank < len(c):
                seen.setdefault(c[rank], None)
    return list(seen)


async def resolve(api: Tauric, ids: list[str]) -> list[dict[str, Any]]:
    """The first `MAX_SOURCES` cited articles, each as `article` gives it; an article Tauric no
    longer serves, or one without a link, is left out."""

    async def one(i: str) -> dict[str, Any] | None:
        try:
            a = article(await api.get(f"/news/{i}"))
        except ToolError:
            return None
        return a if a and a.get("url") else None  # an article without a link is no source

    got = await asyncio.gather(*(one(i) for i in ids[:MAX_SOURCES]))
    return [a for a in got if a]


async def sourced(
    api: Tauric, content: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]], dict[str, int | str]]:
    """The articles a digest's or thesis's claims cite: their ids, those resolved, and the
    numbers the claims cite them by (`number`)."""
    ids = cited_ids(content)
    sources = await resolve(api, ids)
    return ids, sources, number(sources)


_LEAD_CHARS = 160  # a rail line or two


def lead(summary: str | None) -> str:
    """The start of an article's summary, for the rail."""
    text = summary or ""
    return text if len(text) <= _LEAD_CHARS else text[: _LEAD_CHARS - 1].rstrip() + "…"


def number(items: list[dict[str, Any]]) -> dict[str, int | str]:
    """Add each article to the run's sources and set its `source`, the number claims cite it
    by: its `[N]` in a run (none for an article without a link), its Tauric id outside one.
    Returns a map of id to that number."""
    out = {}
    run = in_run()
    for a in items:
        n = cite_source(a.get("url") or "", a.get("title"), lead(a.get("summary")))
        a["source"] = n if run else a.get("id")
        if a.get("id") is not None and a["source"] is not None:
            out[a["id"]] = a["source"]
    return out


_ID = r"art_[0-9A-Za-z]+"
_CITATION = re.compile(rf"\s*\(({_ID}(?:\s*,\s*{_ID})*)\)")


def renumber(text: str, numbers: dict[str, int | str]) -> str:
    """Tauric's inline `(art_a, art_b)` citations in `text` as the numbers `number` gave the
    articles (the run's `[N, M]`, or the ids themselves outside a run); a cited article that
    could not be resolved drops out. Unchanged when no article was numbered."""
    if not numbers:
        return text

    def swap(m: re.Match[str]) -> str:
        ids = [x.strip() for x in m.group(1).split(",")]
        ns = sorted({numbers[i] for i in ids if i in numbers})
        return f" [{', '.join(map(str, ns))}]" if ns else ""

    return _CITATION.sub(swap, text)


def claim(text: str | None, ids: list[str], numbers: dict[str, int | str]) -> str:
    """A claim's text with its sources' numbers: its inline citations renumbered, or, when it
    cites none inline, its `article_ids` appended (Tauric's ids when none was numbered)."""
    text = text or ""
    if _CITATION.search(text):
        return renumber(text, numbers)
    ns = [numbers[i] for i in ids if i in numbers] if numbers else ids
    return f"{text} [{', '.join(map(str, ns))}]" if ns else text


async def dated(api: Tauric, path: str, day: dt.date | None) -> tuple[dict[str, Any], str | None]:
    """A digest for `day`, or the latest with a note when that day has none."""
    if day is None:
        return await api.get(path), None
    try:
        return await api.get(path, date=day.isoformat()), None
    except ToolError as err:
        if err.code not in NOT_PUBLISHED:
            raise
    body = await api.get(path)
    return body, f"No digest on {day}; this is the latest, {body.get('digest_date')}."
