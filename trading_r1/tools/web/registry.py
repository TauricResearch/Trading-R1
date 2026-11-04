# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The research trio's registry: search, visit and compose_citations bound to one cache, and the
finalizer that numbers the sources the answer cites. One cache per run, so no state is shared
across runs. The whole toolkit is assembled in `trading_r1.assembly`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from trading_r1.providers import ModelProfile
from trading_r1.runtime.tool_registry import ToolRegistry
from trading_r1.state.runtime_config import RuntimeConfig
from trading_r1.tools.web.cache import FetchCache
from trading_r1.tools.web.citations import build_citations_spec
from trading_r1.tools.web.references import validate_and_append_references
from trading_r1.tools.web.search import build_search_spec
from trading_r1.tools.web.search_engines import SearchEngine
from trading_r1.tools.web.visit import build_visit_spec
from trading_r1.tools.web.visit_fetch import Fetcher, quick_fetch


def build_research_registry(
    cache: FetchCache,
    reader_profile: ModelProfile,
    *,
    engine: SearchEngine | None = None,
    fetcher: Fetcher | None = None,
    client: AsyncOpenAI | None = None,
    config: RuntimeConfig | None = None,
) -> ToolRegistry:
    """A registry with search + visit + compose_citations bound to ``cache``.

    ``reader_profile`` is the model `visit` uses for page extraction, a
    separate model from the orchestrator's.
    """
    # search pre-flight validation uses the same fetcher as visit when injected
    # (tests), else a quick single-attempt fetch in production.
    registry = ToolRegistry()
    registry.register(build_search_spec(cache, engine, validator=fetcher or quick_fetch))
    registry.register(
        build_visit_spec(cache, reader_profile, fetcher=fetcher, client=client, config=config)
    )
    registry.register(build_citations_spec(cache))
    return registry


def citation_finalizer(cache: FetchCache) -> Callable[[str], tuple[str, dict[str, Any]]]:
    """An Agent ``finalize`` hook that appends the bibliography from ``cache``."""
    return lambda text: validate_and_append_references(text, cache.citations)
