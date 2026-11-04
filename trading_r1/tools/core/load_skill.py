# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""load_skill: pull a workflow recipe into context + auto-load its tools.

Returns each requested skill's SKILL.md body (the recipe the model now follows)
and auto-loads the schemas of every tool in the skill's `requires_tools`, so the
model doesn't need a separate load_tool call.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolSpec, hint
from trading_r1.contract.units import Code, Text
from trading_r1.runtime.skill_registry import SkillRegistry, SkillSessionState
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState

_ALL = 1000  # list them all


class LoadSkillRequest(BaseModel):
    """Input for loading workflow recipes into the current run."""

    skills: list[str] = Field(
        ...,
        min_length=1,
        description="Skill names to load (kebab-case from the skills catalog), "
        "e.g. ['dcf-valuation']. Batch them.",
    )


class LoadedSkill(BaseModel):
    name: Code
    tools_loaded: list[Code] = []  # its required tools, callable now
    recipe: Text  # the SKILL.md body to follow


class LoadSkillOutput(Output):
    loaded: list[Code]
    skills: list[LoadedSkill]
    unknown: list[Code] = []
    # every one, when a name was not found
    available: Annotated[list[Code], hint(rows=_ALL)] = []


def build_load_skill_spec(
    skill_state: SkillSessionState,
    tool_state: ToolSessionState,
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
) -> ToolSpec:
    """Build the per-run `load_skill` tool bound to the run's skill + tool state."""

    async def handler(req: LoadSkillRequest) -> Result:
        skills: list[LoadedSkill] = []
        loaded: list[str] = []
        unknown: list[str] = []
        for name in req.skills:
            spec = skill_registry.get(name)
            if spec is None:
                unknown.append(name)
                continue
            skill_state.loaded.add(name)
            auto: list[str] = []
            if spec.requires_tools:
                # auto-load required tools; silently skip any not registered
                known = [t for t in spec.requires_tools if tool_registry.get(t) is not None]
                if known:
                    tool_state.load(known, tool_registry)
                    auto = known
            skills.append(LoadedSkill(name=name, tools_loaded=auto, recipe=spec.body))
            loaded.append(name)
        available = list(skill_registry.names()) if unknown else []
        return Result(
            LoadSkillOutput(loaded=loaded, skills=skills, unknown=unknown, available=available)
        )

    return ToolSpec(
        name="load_skill",
        display_name="core.load_skill",
        result_kind="control",
        tier="core",
        description=(
            "Load one or more workflow-recipe skills (kebab-case from the skills catalog) "
            "when the question matches. Each skill's required tools are auto-loaded."
        ),
        request_schema=LoadSkillRequest,
        handler=handler,
        output=LoadSkillOutput,
        tags=("core", "meta"),
        evidence=False,
    )
