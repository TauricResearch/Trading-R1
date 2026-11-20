# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""data.equity.digest: Tauric's machine-written read of a name: the daily digest or the thesis.

The daily digest is the day's news on the name, summarized into bullets (each with its polarity
and event type) and items to watch. The thesis is a running synthesis across the digests: a
narrative, drivers, risks, dated catalysts and open questions. Both are machine-generated from
news Tauric collected; every bullet cites its articles, which the tool resolves to links. They
are leads to verify, not sources: the model opens the articles it relies on.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Provenance, Result, ToolError, ToolSpec, rendered
from trading_r1.contract.units import Code, Count, Day, Text
from trading_r1.tools.data import articles
from trading_r1.tools.data.articles import Cited, dated
from trading_r1.tools.data.common import Symbol
from trading_r1.tools.data.tauric import Tauric, provenance

_DESCRIPTION = (
    "Tauric's machine-written read of one stock from the news it collects (the market-wide "
    "digest is data.market.digest). view digest "
    "(default): one day's summary, with headline, bullets each with polarity (positive/negative/"
    "neutral) and event type, and items to watch, for the latest day or `date`. view thesis: "
    "the running synthesis (narrative, drivers, risks, dated catalysts, open questions). Each "
    "claim cites its articles as numbered sources [N], listed with links: cite a claim by its "
    "articles' numbers, and open them with `visit` when the summary is not enough (the digest "
    "is machine-written; the articles are the sources)."
)
View = Literal["digest", "thesis"]


class DigestRequest(BaseModel):
    """Input for a name's digest or thesis."""

    symbol: Symbol = Field(..., description="Ticker (e.g. AAPL).")
    view: View = Field("digest", description="digest (one day) or thesis (the running view).")
    date: dt.date | None = Field(
        None, description="digest view only: the day (default the latest digest)."
    )


class Bullet(BaseModel):
    polarity: Code = None
    event_type: Code = None
    text: Text  # its sources' numbers [N] at the end


class Catalyst(BaseModel):
    date: Day = None
    timing: Code = None  # as the source states it, when there is no date
    timing_status: Code = None
    text: Text


class DigestOutput(Output):
    """One day's digest (headline, bullets, watch items) or the thesis (narrative, drivers,
    risks, catalysts, open questions); each claim ends with its sources' numbers."""

    model_config = rendered(then=articles.VERIFY)

    ticker: Code
    view: Code
    date: Day = None
    evidence_cutoff: Day = None
    note: Text = None  # the day asked for had no digest
    headline: Text = None
    bullets: list[Bullet] = []
    watch_items: list[Text] = []
    narrative: Text = None
    drivers: list[Text] = []
    risks: list[Text] = []
    catalysts: list[Catalyst] = []
    open_questions: list[Text] = []
    cited: Count  # articles the claims cite; `sources` holds those resolved
    sources: list[Cited]


def _claims(content: dict[str, Any], numbers: dict[str, int | str]) -> dict:
    """The view's claims, each text ending with its sources' numbers: the run's [N] for the
    resolved articles inside a run, Tauric's ids outside one."""

    def text(x: dict[str, Any]) -> str:
        return articles.claim(x.get("text"), x.get("article_ids") or [], numbers)

    def prose(x: str | None) -> str | None:
        return articles.renumber(x, numbers) if x else x

    return {
        "headline": prose(content.get("headline")),
        "bullets": [
            {"polarity": b.get("polarity"), "event_type": b.get("event_type"), "text": text(b)}
            for b in content.get("bullets") or []
        ],
        "watch_items": [prose(x) for x in content.get("watch_items") or []],
        "narrative": prose(content.get("narrative")),
        "drivers": [text(x) for x in content.get("drivers") or []],
        "risks": [text(x) for x in content.get("risks") or []],
        "catalysts": [
            {
                "date": c.get("date"),
                "timing": None if c.get("date") else c.get("timing_stated"),
                "timing_status": c.get("timing_status"),
                "text": text(c),
            }
            for c in content.get("catalysts") or []
        ],
        "open_questions": [prose(x) for x in content.get("open_questions") or []],
    }


async def _handler(req: DigestRequest) -> Result:
    ticker = req.symbol
    async with Tauric() as api:
        if req.view == "digest":
            body, note = await dated(api, f"/securities/{ticker}/digest", req.date)
        else:
            note = None
            body = await api.get(f"/securities/{ticker}/thesis")
        content = body.get("content") or {}
        if not content:
            raise ToolError(f"no {req.view} for '{ticker}'")
        ids, sources, numbers = await articles.sourced(api, content)
    cutoff = (body.get("evidence") or {}).get("cutoff")
    out = DigestOutput(
        ticker=ticker,
        view=req.view,
        date=body.get("digest_date"),
        evidence_cutoff=cutoff,
        note=note,
        cited=len(ids),
        sources=sources,
        **_claims(content, numbers),
    )
    return Result(
        out,
        Provenance(
            **provenance(
                subject=ticker,
                view=req.view,
                as_of=body.get("digest_date") or cutoff,
                source="Tauric digest",
            )
        ),
    )


SPEC = ToolSpec(
    name="data_equity_digest",
    display_name="data.equity.digest",
    result_kind="data",
    tier="deferred",
    description=_DESCRIPTION,
    request_schema=DigestRequest,
    handler=_handler,
    output=DigestOutput,
    tags=("equity", "news", "digest", "thesis", "catalysts", "data"),
)
