# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""FetchCache: per-run shared state for the research tools.

`search` records discovered titles; `visit` records fetched page content and
appends citations; `compose_citations` reads the citation index. One cache per
ReAct loop, threaded into the tool factories, so the trio coordinate without
global state.

The citation index is the single source of the bibliography: a URL is cited when
`visit` read an accessible page, or when a data tool returned the article from Tauric's news
store (title, summary and link: the article as the model saw it), in the order they arrive,
deduped by URL. A run binds its cache with ``runtime.sources.source_scope``, so a data tool
cites through ``cite_source`` without the cache threaded into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Citation:
    """One source in the conversation's bibliography."""

    index: int  # 1-based, the [N] marker
    url: str
    title: str
    record: str | None = None  # the evidence record that first produced the source


@dataclass
class FetchCache:
    """Run-local page-content, title, and citation cache shared by research tools."""

    _content: dict[str, str] = field(default_factory=dict)
    _titles: dict[str, str] = field(default_factory=dict)
    _citations: list[Citation] = field(default_factory=list)
    _cited_index: dict[str, int] = field(default_factory=dict)

    def seed(self, citations: list[Citation]) -> None:
        """Start from a conversation's sources: each keeps its number."""
        for cited in citations:
            self._citations.append(cited)
            if cited.title != cited.url:
                self.set_title(cited.url, cited.title)
            self._cited_index[cited.url] = cited.index

    # titles (set by search + visit) -------------------------------------- #
    def set_title(self, url: str, title: str | None) -> None:
        if title:
            self._titles[url] = title

    def get_title(self, url: str) -> str | None:
        return self._titles.get(url)

    # content (set by visit; read on cache hit) --------------------------- #
    def set_content(self, url: str, text: str) -> None:
        self._content[url] = text

    def get_content(self, url: str) -> str | None:
        return self._content.get(url)

    def has_content(self, url: str) -> bool:
        return url in self._content

    # citations (appended by visit; read by compose_citations) ------------ #
    def add_citation(self, url: str, title: str | None = None) -> int:
        """Add `url` to the bibliography (dedup by URL, in the order sources arrive). Returns
        its 1-based marker index."""
        self.set_title(url, title)
        if url in self._cited_index:
            return self._cited_index[url]
        index = len(self._citations) + 1
        self._citations.append(
            Citation(index=index, url=url, title=title or self._titles.get(url) or url)
        )
        self._cited_index[url] = index
        return index

    def link(self, index: int, record: str) -> None:
        """Link source `index` to the evidence record that produced it (the first one wins)."""
        cited = self._citations[index - 1]
        if cited.record is None:
            self._citations[index - 1] = replace(cited, record=record)

    @property
    def citations(self) -> list[Citation]:
        return list(self._citations)
