# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The `compose_citations` core tool: hand the model its numbered bibliography.

Returns the visit-order, URL-deduped citation list so the model can place inline
`[N]` markers in its report. The harness appends the matching `## References`
block at finalize regardless, so this tool is the model's cue for *where* to
cite, not the bibliography's source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count, Text
from trading_r1.tools.web.cache import FetchCache
from trading_r1.tools.web.references import format_references_block

_DESCRIPTION = (
    "Return the numbered list of sources so far: pages read with `visit` and articles the "
    "news and digest tools returned (in the order they came, deduped). A source keeps its "
    "number for the whole conversation. Inline [N] markers use these numbers; the harness "
    "appends the matching References section automatically."
)

_WRITE_INSTRUCTION = (
    "Inline [N] markers next to a claim drawn from a source use the numbers above. The harness "
    "appends the References section from these sources."
)

_EMPTY_INSTRUCTION = (
    "No sources yet. An answer from prior knowledge is plain prose; the harness adds no "
    "bibliography then."
)


class ComposeCitationsRequest(BaseModel):
    """Input for composing the current run's citation list."""

    pass


class CitationsOutput(Output):
    """The numbered sources (``references``, as the report's References will read) and what
    to do next."""

    count: Count
    references: Text = None
    next: Text


def build_citations_spec(cache: FetchCache) -> ToolSpec:
    """Build the per-run `compose_citations` tool bound to the cache."""

    async def handler(_req: ComposeCitationsRequest) -> Result:
        citations = cache.citations
        if not citations:
            return Result(CitationsOutput(count=0, next=_EMPTY_INSTRUCTION))
        return Result(
            CitationsOutput(
                count=len(citations),
                references=format_references_block(citations),
                next=_WRITE_INSTRUCTION,
            )
        )

    return ToolSpec(
        name="compose_citations",
        display_name="web.compose_citations",
        result_kind="web",
        tier="core",
        description=_DESCRIPTION,
        request_schema=ComposeCitationsRequest,
        handler=handler,
        output=CitationsOutput,
        tags=("research", "citations"),
        evidence=False,
    )
