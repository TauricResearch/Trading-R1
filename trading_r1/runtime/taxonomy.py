# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Capability taxonomy: the family/domain classification the execution modes group on.

Two axes:
- family, the structural role: ``web`` and ``core`` are the always-on *foundation*
  (search/visit/cite; calc/plan/loaders); ``data`` and ``compute`` are the deferred
  domain packs; ``workflow`` is skills.
- domain, the subject area (``equity``, ``valuation``, ``risk``, …), carried by
  ``data``/``compute`` tools and declared by skills.

The classification is derived from a tool's ``display_name`` (which follows the
grammar ``family.name`` (foundation) or ``family.domain.name`` (domain packs)) and is
validated (see :func:`classify_issues`): a namespace that doesn't resolve to a known
family/domain fails a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_r1.contract.spec import ToolSpec
    from trading_r1.runtime.skill_registry import SkillRegistry, SkillSpec
from trading_r1.runtime.tool_registry import ToolRegistry

# Structural families (the first segment of a tool's display_name).
FAMILIES: frozenset[str] = frozenset({"web", "core", "data", "compute", "workflow"})
# The always-on foundation (tier=core): web research + core utilities. Never backgrounded.
FOUNDATION_FAMILIES: frozenset[str] = frozenset({"web", "core"})
# The deferred domain packs: these are what a mode foregrounds or backgrounds.
PACK_FAMILIES: frozenset[str] = frozenset({"data", "compute", "workflow"})


@dataclass(frozen=True)
class DisciplineSpec:
    """A top-level financial discipline: the practitioner-facing grouping several subject
    domains roll up to (fundamental vs quantitative vs FICC vs markets). The hierarchy the
    tools/skills browser reads; the prompt catalog stays domain-flat."""

    title: str
    order: int


# Top-level financial disciplines (fundamental, quantitative, FICC, markets).
# Each subject domain rolls up to exactly one.
DISCIPLINE_SPECS: dict[str, DisciplineSpec] = {
    "fundamental": DisciplineSpec("Fundamental Analysis", 10),
    "quantitative": DisciplineSpec("Quantitative & Risk", 20),
    "ficc": DisciplineSpec("Fixed Income & Derivatives", 30),
    "markets": DisciplineSpec("Markets & Economics", 40),
}
DISCIPLINES: frozenset[str] = frozenset(DISCIPLINE_SPECS)


@dataclass(frozen=True)
class DomainSpec:
    """One subject domain: its display title, catalog ordering, and the financial discipline
    it rolls up to. The single source of truth for the domain taxonomy."""

    title: str
    order: int
    discipline: str  # a key in DISCIPLINE_SPECS


# Subject domains (the second segment of a data/compute display_name; declared by skills).
# Orders are discipline-contiguous so the flat prompt catalog and the grouped browser read
# in the same order.
DOMAIN_SPECS: dict[str, DomainSpec] = {
    "equity": DomainSpec("Equity", 10, "fundamental"),
    "valuation": DomainSpec("Valuation", 20, "fundamental"),
    "modeling": DomainSpec("Financial modeling", 25, "fundamental"),
    "statistics": DomainSpec("Statistics", 30, "quantitative"),
    "technical": DomainSpec("Technical analysis", 35, "quantitative"),
    "risk": DomainSpec("Risk", 40, "quantitative"),
    "portfolio": DomainSpec("Portfolio", 50, "quantitative"),
    "fixed_income": DomainSpec("Fixed income", 60, "ficc"),
    "derivatives": DomainSpec("Derivatives", 70, "ficc"),
    "market": DomainSpec("Markets", 80, "markets"),
    "macro": DomainSpec("Economics", 85, "markets"),
}
DOMAINS: frozenset[str] = frozenset(DOMAIN_SPECS)


def classify_tool(spec: ToolSpec) -> tuple[str, str | None]:
    """``(family, domain)`` for a tool, derived from its ``display_name`` namespace.

    A foundation tool (``web.*``/``core.*``) has a family and no domain. A domain-pack tool
    (``data.<domain>.*`` / ``compute.<domain>.*``) carries both.
    """
    parts = spec.display_name.split(".")
    family = parts[0]
    domain = parts[1] if family in ("data", "compute") and len(parts) >= 3 else None
    return family, domain


def classify_skill(spec: SkillSpec) -> tuple[str, str | None]:
    """``("workflow", domain)`` for a skill: its declared ``domain`` (or ``None``)."""
    return "workflow", (spec.domain or None)


def classify_issues(
    registry: ToolRegistry, skill_registry: SkillRegistry | None = None
) -> list[str]:
    """Every tool/skill must resolve to a known family (and, for a domain pack, a known
    domain). Returns a list of human-readable issues, empty when the taxonomy is total.
    Used by a test, so naming drift fails."""
    issues: list[str] = []
    for spec in [*registry.core_tools(), *registry.deferred_tools()]:
        family, domain = classify_tool(spec)
        if family not in FAMILIES:
            issues.append(f"tool {spec.display_name!r}: unknown family {family!r}")
        if family in ("data", "compute"):
            if domain is None:
                issues.append(f"tool {spec.display_name!r}: {family} tool without a domain segment")
            elif domain not in DOMAINS:
                issues.append(f"tool {spec.display_name!r}: unknown domain {domain!r}")
    if skill_registry is not None:
        for name in skill_registry.names():
            sk = skill_registry.get(name)
            if not sk.domain:
                issues.append(f"skill {name!r}: no `domain` declared")
            elif sk.domain not in DOMAINS:
                issues.append(f"skill {name!r}: unknown domain {sk.domain!r}")
    return issues
