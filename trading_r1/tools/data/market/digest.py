# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.market.digest: Tauric's machine-written read of the day's market.

The day's market news summarized: a headline, themes (each a short synthesis), top stories and
items to watch, every claim citing its articles, which the tool resolves to links. A lead to
verify, not a source.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from trading_r1.contract.spec import (
    Output,
    Provenance,
    Result,
    ToolError,
    ToolSpec,
    rendered,
)
from trading_r1.contract.units import Code, Day, Text
from trading_r1.tools.data import articles
from trading_r1.tools.data.articles import Cited
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Market-wide daily news digest, not one stock's (data.equity.digest is per ticker): "
    "Tauric's machine-written summary of a day's market news, with headline, themes (earnings, "
    "inflation and rates, sectors, …) each with a short synthesis, top stories with polarity "
    "and event type, and items to watch, for the latest day or `date`. Claims cite their "
    "articles as numbered sources [N], listed with links: cite a claim by its articles' "
    "numbers, and open them with `visit` when the summary is not enough (the digest is "
    "machine-written; the articles are the sources)."
)
_VERIFY = "Machine-written from the day's articles: cite the sources [N], verify through them."


class MarketDigestRequest(BaseModel):
    """Input for the market digest."""

    date: dt.date | None = Field(None, description="The day (default the latest).")


class Theme(BaseModel):
    name: Code
    summary: Text


class Story(BaseModel):
    polarity: Code = None
    event_type: Code = None
    text: Text  # closing with the articles it cites, [N]
    instruments: list[Code] = []


class DigestContent(BaseModel):
    headline: Text = None
    themes: list[Theme] = []
    top_stories: list[Story] = []
    watch_items: list[Text] = []
    instruments: list[Code] = []


class MarketDigestOutput(Output):
    """The digest with its claims' citations as the run's source numbers `[N]` (Tauric's
    article ids outside a run), and the cited articles under those numbers."""

    model_config = rendered(then=_VERIFY)

    date: Day
    note: Text = None  # the requested day had no digest
    authorship: Code = None
    content: DigestContent
    sources: list[Cited]


def _content(raw: dict[str, Any], cite: Callable[[str], str]) -> DigestContent:
    """The digest's content with every cited `[id]` read as its source number."""
    return DigestContent(
        headline=raw.get("headline"),
        themes=[
            Theme(
                name=t.get("name") or "",
                summary=cite(t.get("summary") or ""),
            )
            for t in raw.get("themes") or []
        ],
        top_stories=[
            Story(
                polarity=x.get("polarity"),
                event_type=x.get("event_type"),
                text=cite((x.get("text") or "") + articles.cites(x)),
                instruments=x.get("instruments") or [],
            )
            for x in raw.get("top_stories") or []
        ],
        watch_items=[cite(w) for w in raw.get("watch_items") or []],
        instruments=raw.get("instruments") or [],
    )


async def _handler(req: MarketDigestRequest) -> Result:
    async with Tauric() as api:
        body, note = await articles.dated(api, "/digests/market", req.date)
        content = body.get("content") or {}
        if not content:
            raise ToolError("no market digest for that day")
        ids = articles.cited_ids(content)
        sources = await articles.resolve(api, ids)
    numbers = articles.number(sources)
    cited = set(ids)

    def cite(text: str) -> str:
        return articles.renumber(text, numbers, cited)

    out = MarketDigestOutput(
        date=body.get("digest_date"),
        note=note,
        authorship=body.get("authorship"),
        content=_content(content, cite),
        sources=sources,
    )
    return Result(
        out,
        Provenance(
            **provenance(subject="market", as_of=body.get("digest_date"), source="Tauric digest")
        ),
    )


SPEC = ToolSpec(
    name="data_market_digest",
    display_name="data.market.digest",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=MarketDigestRequest,
    handler=_handler,
    output=MarketDigestOutput,
    tags=("market", "news", "digest", "themes", "data"),
)
