# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What the page and the PDF share: the markdown dialect, the link rule and the date. A message
made from the same report applies the same link rule (``cited_urls``)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from trading_r1.report.snapshot import ReportSnapshot

MARKER = re.compile(r"\[(\d{1,3})\]")
SAFE_SCHEMES = frozenset({"http", "https"})


def markdown() -> MarkdownIt:
    """The answer's markdown: raw HTML and images off, tables and strikethrough on."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    return md.enable(["table", "strikethrough"]).disable("image")


def scheme(url: str) -> str:
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:  # a malformed address is kept as text
        return ""


def when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def cited_urls(snap: ReportSnapshot) -> frozenset[str]:
    """The pages a link may point to: those the run read, else those the answer lists."""
    return frozenset(snap.cited) or frozenset(s.url for s in snap.sources if s.url)


def link_scope(snap: ReportSnapshot, links: Literal["web", "cited"]) -> frozenset[str] | None:
    """``None``: every http(s) link is kept (``"web"``); else only the cited pages."""
    return cited_urls(snap) if links == "cited" else None


def link_allowed(url: str, cited: frozenset[str] | None) -> bool:
    """An http(s) address, and, when ``cited`` is given, one of the cited pages."""
    return scheme(url) in SAFE_SCHEMES and (cited is None or url in cited)
