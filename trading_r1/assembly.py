# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Assembly: the one place the toolkit and the skills are put together.

`build_tool_registry` registers every built-in tool; `build_run_registry` adds the tools bound to
one run's own state; `run_binder` hands that to an `Agent`; the skill registry discovers the
bundled and drop-in skills. Everything is built fresh per call, so no state is shared across runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from trading_r1.providers import ModelProfile, get_profile
from trading_r1.runtime.agent import Bind, loaded_earlier
from trading_r1.runtime.context_budget import estimate_tokens, schema_tokens
from trading_r1.runtime.memory import MemoryStore
from trading_r1.runtime.modes import get_mode
from trading_r1.runtime.prompts import build_system_prompt
from trading_r1.runtime.run_state import EventCallback, RunContext, RunPolicy
from trading_r1.runtime.skill_registry import (
    SkillRegistry,
    SkillSessionState,
    load_skill_registry,
    merge_skills,
)
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState
from trading_r1.state.runtime_config import RuntimeConfig
from trading_r1.tools.compute import COMPUTE_SPECS
from trading_r1.tools.compute.arithmetic import SPEC as CALC_SPEC
from trading_r1.tools.core.plan import SPEC as PLAN_SPEC
from trading_r1.tools.data.equity import EQUITY_DATA_SPECS
from trading_r1.tools.data.macro import MACRO_DATA_SPECS
from trading_r1.tools.data.market import MARKET_DATA_SPECS
from trading_r1.tools.web import FetchCache, build_research_registry
from trading_r1.tools.web.search_engines import SearchEngine
from trading_r1.tools.web.visit_fetch import Fetcher

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"


def build_tool_registry(
    cache: FetchCache,
    reader_profile: ModelProfile,
    *,
    engine: SearchEngine | None = None,
    fetcher: Fetcher | None = None,
    client: Any | None = None,
    config: RuntimeConfig | None = None,
) -> ToolRegistry:
    """Every built-in tool: the research trio, exact arithmetic and the plan as core tools, and the
    deferred equity, market and macro data and compute tools."""
    registry = build_research_registry(
        cache, reader_profile, engine=engine, fetcher=fetcher, client=client, config=config
    )
    registry.register(CALC_SPEC)  # core: exact arithmetic so the model never computes in its head
    registry.register(PLAN_SPEC)  # core: a live research plan/checklist for multi-step work
    for spec in (*EQUITY_DATA_SPECS, *MARKET_DATA_SPECS, *MACRO_DATA_SPECS, *COMPUTE_SPECS):
        registry.register(spec)
    return registry


def catalog_tool_registry() -> ToolRegistry:
    """Every built-in tool, for catalog views (the /capabilities endpoint): a fresh cache and the
    default reader, no run state."""
    return build_tool_registry(FetchCache(), get_profile("vllm-reader", env={}))


def build_run_registry(
    base: ToolRegistry,
    skill_registry: SkillRegistry | None,
    session_state: ToolSessionState,
    skill_state: SkillSessionState,
    emit: EventCallback,
    *,
    elicit_timeout_s: float = 0.0,
    memory: MemoryStore | None = None,
    policy: RunPolicy = RunPolicy.INTERACTIVE,
) -> ToolRegistry:
    """The registry the model sees in one run: a clone of ``base`` plus the per-run tools bound to
    this run's state: evidence (reading back recorded results), the
    sensitivity sweep (when there are compute tools; it dispatches within this registry),
    load_tool (when there are deferred tools), load_skill (when there are skills), ask_user
    (mid-run clarification, in the terminal only) and remember/forget (when ``memory`` is given,
    in a conversation with the user: a scheduled run reads the notes but cannot change them).
    Never mutates ``base``."""
    from trading_r1.runtime.elicitation import Elicitor
    from trading_r1.tools.compute.modeling.sensitivity import build_sensitivity_spec
    from trading_r1.tools.core import (
        build_ask_user_spec,
        build_evidence_spec,
        build_load_skill_spec,
        build_load_tool_spec,
        build_memory_specs,
    )

    registry = base.clone()
    registry.register(build_evidence_spec(session_state.evidence))
    if any(s.result_kind == "compute" for s in registry.deferred_tools()):
        registry.register(build_sensitivity_spec(session_state, registry))
    if registry.deferred_tools():
        registry.register(build_load_tool_spec(session_state, registry))
    if skill_registry and skill_registry.all():
        registry.register(
            build_load_skill_spec(skill_state, session_state, skill_registry, registry)
        )
    if policy is RunPolicy.INTERACTIVE:
        registry.register(build_ask_user_spec(Elicitor(emit, elicit_timeout_s)))
    if memory is not None and policy is not RunPolicy.SCHEDULED:  # the user is there to tell it
        for spec in build_memory_specs(memory):
            registry.register(spec)
    return registry


def run_binder(skill_registry: SkillRegistry | None) -> Bind:
    """What an `Agent` is given to bind each run's own state into its registry."""

    def bind(base: ToolRegistry, run: RunContext) -> ToolRegistry:
        return build_run_registry(
            base,
            skill_registry,
            run.session_state,
            run.skill_state,
            run.emit,
            elicit_timeout_s=run.elicit_timeout_s,
            memory=run.memory,
            policy=run.policy,
        )

    return bind


def model_facing_registry(skills: SkillRegistry) -> ToolRegistry:
    """The registry a run would expose: the catalog tools plus the per-run tools (the
    sensitivity sweep and the meta-tools), with fresh state, for catalog views."""
    return build_run_registry(
        catalog_tool_registry(),
        skills,
        ToolSessionState(),
        SkillSessionState(),
        lambda *_: None,
        memory=MemoryStore(),
    )


def _project_roots(dirname: str, env_var: str) -> list[Path]:
    """Where user/project specs are discovered, in increasing precedence: the project's
    ``./<dirname>/`` (CWD), then any roots in ``env_var`` (os.pathsep-separated). Later
    roots override built-ins by name, so skills are extensible without code."""
    roots = [Path.cwd() / dirname]
    env = os.environ.get(env_var)
    if env:
        roots += [Path(p) for p in env.split(os.pathsep) if p.strip()]
    return roots


def builtin_skill_registry() -> SkillRegistry:
    """The bundled first-party skills only (strict), independent of the working
    directory and environment, for catalog views that must be reproducible."""
    return load_skill_registry(_SKILLS_ROOT)


def default_skill_registry() -> SkillRegistry:
    """Bundled first-party skills + any drop-in project/user skills (./skills,
    TR1_SKILLS_DIR), the latter overriding by name. A skill is a SKILL.md recipe that
    orchestrates existing tools, so user skills are safe by construction."""
    reg = builtin_skill_registry()
    for root in _project_roots("skills", "TR1_SKILLS_DIR"):
        if root.resolve() != _SKILLS_ROOT.resolve():  # don't re-scan the built-in root
            merge_skills(reg, root, strict=False)  # best-effort, override
    return reg


def prompt_overhead(
    history: list[dict[str, Any]] | None,
    *,
    skills: SkillRegistry,
    mode: str | None = None,
    memory: MemoryStore | None = None,
) -> int:
    """The tokens a session's next request carries beside its history: the system prompt and the
    schemas of the core tools and of those its history loaded."""
    session = ToolSessionState()
    registry = build_run_registry(
        catalog_tool_registry(),
        skills,
        session,
        SkillSessionState(),
        lambda *_: None,
        memory=memory,
    )
    session.load(loaded_earlier(history, skills), registry)
    prompt = build_system_prompt(
        registry,
        skill_registry=skills,
        mode=get_mode(mode),
        memory=memory.block() if memory else "",
    )
    system = estimate_tokens([{"role": "system", "content": prompt}])
    return system + schema_tokens(session.visible_schemas(registry))
