# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""load_tool: fetch deferred-tool schemas on demand.

The model sees deferred tools as catalog lines only. Calling load_tool marks
them loaded in the session state, so the next request's `tools` carries their schemas.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolSpec, hint
from trading_r1.contract.units import Code
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState

_ALL = 1000  # list them all


class LoadToolRequest(BaseModel):
    """Input for loading deferred tool schemas into the current run."""

    tools: list[str] = Field(
        ...,
        min_length=1,
        description="Deferred tool wire-names to load, e.g. ['data_equity_reference']. Batch them.",
    )


class LoadToolOutput(Output):
    """The tools now callable; their schemas come with the next request. Names not found, with
    the deferred tools there are."""

    loaded: list[Code]
    unknown: list[Code] = []
    # every one, when a name was not found
    known: Annotated[list[Code], hint(rows=_ALL)] = []


def build_load_tool_spec(session_state: ToolSessionState, registry: ToolRegistry) -> ToolSpec:
    """Build the per-run `load_tool` tool bound to the run's session state."""

    async def handler(req: LoadToolRequest) -> Result:
        loaded, unknown = session_state.load(req.tools, registry)
        known = sorted(s.name for s in registry.deferred_tools()) if unknown else []
        return Result(
            LoadToolOutput(
                loaded=[s.name for s in loaded],
                unknown=unknown,
                known=known,
            )
        )

    return ToolSpec(
        name="load_tool",
        display_name="core.load_tool",
        result_kind="control",
        tier="core",
        description=(
            "Load schemas for one or more deferred tools (use the underscored catalog "
            "names) so they can be called on the next turn. Batch all you need into one call."
        ),
        request_schema=LoadToolRequest,
        handler=handler,
        output=LoadToolOutput,
        tags=("core", "meta"),
        evidence=False,
    )
