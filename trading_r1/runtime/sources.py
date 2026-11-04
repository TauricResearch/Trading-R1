# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The run's numbered sources, reachable from any tool.

A run binds its bibliography (the research tools' cache) with :func:`source_scope`; a tool adds
a source with :func:`cite_source` (visit for a page it read, a data tool for an article it
returned), which numbers it, and shows it in the rail through the tool's event channel. The
dispatcher collects the sources a call cites (:func:`source_batch`) and, once it has recorded the
call as evidence, links each to that record: a source is the evidence record that first
produced it, so `[N]` in an answer resolves to a record id.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Protocol

from trading_r1.contract import events
from trading_r1.runtime.tool_events import emit_tool_event


class Bibliography(Protocol):
    def add_citation(self, url: str, title: str | None = None) -> int: ...

    def link(self, index: int, record: str) -> None:
        """Link source `index` to the evidence record that produced it (the first one wins)."""
        ...


_active: ContextVar[Bibliography | None] = ContextVar("tr1_sources", default=None)
_batch: ContextVar[list[tuple[Bibliography, int]] | None] = ContextVar("tr1_batch", default=None)


@contextlib.contextmanager
def source_scope(bibliography: Bibliography) -> Iterator[None]:
    """Bind the run's bibliography for the duration of the run."""
    token = _active.set(bibliography)
    try:
        yield
    finally:
        _active.reset(token)


@contextlib.contextmanager
def source_batch() -> Iterator[list[tuple[Bibliography, int]]]:
    """Collect the sources cited while a tool call runs, as (bibliography, index) pairs."""
    cited: list[tuple[Bibliography, int]] = []
    token = _batch.set(cited)
    try:
        yield cited
    finally:
        _batch.reset(token)


def in_run() -> bool:
    """Whether a run's bibliography is bound: sources are numbered [N] only inside one."""
    return _active.get() is not None


def cite_source(
    url: str, title: str | None, headline: str, bibliography: Bibliography | None = None
) -> int | None:
    """Add a source to the run's bibliography (deduped by URL) and show it in the rail: its
    `[N]`, or None outside a run (a tool called on its own)."""
    bib = bibliography or _active.get()
    if bib is None or not url:
        return None
    index = bib.add_citation(url, title)
    if (cited := _batch.get()) is not None:
        cited.append((bib, index))
    emit_tool_event(events.Source(index=index, url=url, title=title or url, headline=headline))
    return index
