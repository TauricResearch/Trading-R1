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

from pydantic import BaseModel

from trading_r1.contract.spec import ToolError, hint
from trading_r1.contract.units import Code, Count, Day, Number, Text
from trading_r1.runtime.sources import cite_source, in_run
from trading_r1.tools.data.tauric import Tauric

_SUMMARY_CHARS = 280  # the link carries the article
# A backstop on cited articles resolved per call; past it,
# each claim keeps its first sources before any claim gets a second.
MAX_SOURCES = 80
NEWS_PAGE = 21  # Tauric's most news items per response


class Article(BaseModel):
    """An article as the tools show it: the run's source number `[N]` (its Tauric id outside a
    run), time and its basis (published, or first seen by Tauric), publisher and the
    publishers of syndicated copies (or their count), headline, Tauric's read, link and summary."""

    source: float | int | str | None = None  # what claims cite it by: the run's [N], or its Tauric id
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
    summary = " ".join((e.get("summary") or "").split())
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


def when(item: dict[str, Any]) -> str:
    """An article's time to the minute, saying whether it is the publisher's or the moment
    Tauric first saw the article (the publisher's time is often not stated)."""
    stamp = item.get("published")
    if not stamp:
        return "time n/a"
    try:
        moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.UTC)
    t = moment.strftime("%Y-%m-%d %H:%M UTC")
    return t if item.get("time_basis") == "published" else f"{t} (first seen)"


def cited_ids(*blocks: Any) -> list[int]:
    """Every cited article id under the given content, without repeats, ordered so each claim's
    first source comes before any claim's second (a cap then keeps every claim sourced)."""
    claims: list[list[int]] = []

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
    seen: dict[int, None] = {}
    for rank in range(max((len(c) for c in claims), default=0)):
        for c in claims:
            if rank < len(c):
                seen.setdefault(c[rank], None)
    return list(seen)


async def resolve(api: Tauric, ids: list[int]) -> list[dict[str, Any]]:
    """The first `MAX_SOURCES` cited articles as {id, title, publisher, published, url};
    an article Tauric no longer serves is left out."""

    async def one(i: int) -> dict[str, Any] | None:
        try:
            a = article(await api.get(f"/news/{i}"))
        except ToolError:
            return None
        keep = a and a.get("url")  # an article without a link cannot be a source
        keys = ("id", "title", "publisher", "published", "time_basis", "url", "summary")
        return keep and {k: a[k] for k in keys}

    got = await asyncio.gather(*(one(i) for i in ids[:MAX_SOURCES]))
    return [a for a in got if a]


_LEAD_CHARS = 160  # a rail line or two


def lead(summary: str | None) -> str:
    """An article's first point, for the rail: Tauric's summaries are "- point - point"."""
    text = (summary or "").strip().removeprefix("- ")
    first = text.split(" - ", 1)[0].strip()
    return first if len(first) <= _LEAD_CHARS else first[: _LEAD_CHARS - 1].rstrip() + "…"


def number(items: list[dict[str, Any]]) -> dict[int, int]:
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


_ID_RUN = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def renumber(text: str, numbers: dict[int, int], cited: set[int]) -> str:
    """Tauric's `[id, id]` citations in `text` as the numbers `number` gave the articles (the
    run's `[N, M]`, or the ids themselves outside a run); a cited article that could not be
    resolved drops out. Unchanged when no article was numbered."""
    if not numbers:
        return text

    def swap(m: re.Match[str]) -> str:
        ids = [t.strip() for t in m.group(1).split(",")]
        if not any(i in cited for i in ids):
            return m.group(0)
        ns = sorted({numbers[i] for i in ids if i in numbers}, key=str)
        return f"[{', '.join(map(str, ns))}]" if ns else ""

    return _ID_RUN.sub(swap, text)


def cites(x: dict[str, Any]) -> str:
    """A claim's cited article ids as ` [1, 2]`, or nothing."""
    ids = x.get("article_ids") or []
    return f" [{', '.join(map(str, ids))}]" if ids else ""


async def dated(api: Tauric, path: str, day: dt.date | None) -> tuple[dict[str, Any], str | None]:
    """A digest for `day`, or the latest with a note when that day has none."""
    if day is None:
        return await api.get(path), None
    try:
        return await api.get(path, date=day.isoformat()), None
    except ToolError as err:
        if err.code != "not_found":
            raise
    body = await api.get(path)
    return body, f"No digest on {day}; this is the latest, {body.get('digest_date')}."
