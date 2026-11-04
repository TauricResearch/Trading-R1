# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Structured capability catalog: the browsable, mode-independent view of the harness.

The system prompt renders a *mode-shaped* catalog (foreground/background, prose) via
:func:`trading_r1.runtime.prompts.render_capabilities`. This module renders the same
tools + skills as **structured JSON grouped by domain**, for the TUI's "browse tools &
skills" view. Both share the one taxonomy (:mod:`trading_r1.runtime.taxonomy`), so the
browser and the prompt can never drift on how a capability is classified.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from trading_r1.runtime.taxonomy import (
    DISCIPLINE_SPECS,
    DOMAIN_SPECS,
    FOUNDATION_FAMILIES,
    classify_skill,
    classify_tool,
)

if TYPE_CHECKING:
    from trading_r1.contract.spec import ToolSpec
    from trading_r1.runtime.skill_registry import SkillRegistry, SkillSpec
from trading_r1.runtime.tool_registry import ToolRegistry


def _tool_dict(spec: ToolSpec, family: str, domain: str | None) -> dict[str, Any]:
    return {
        "kind": "tool",
        "name": spec.name,  # wire name (what load_tool takes)
        "display_name": spec.display_name,
        "family": family,
        "domain": domain,
        "tier": spec.tier,
        "description": spec.description,
        "tags": list(spec.tags),
    }


def _skill_dict(spec: SkillSpec, domain: str | None) -> dict[str, Any]:
    return {
        "kind": "skill",
        "name": spec.name,  # skill name (what load_skill takes)
        "display_name": spec.name,
        "family": "workflow",
        "domain": domain,
        "tier": "skill",
        "description": spec.description,
        "requires_tools": list(spec.requires_tools),
        "tags": list(spec.tags),
    }


def capabilities_catalog(
    registry: ToolRegistry, skill_registry: SkillRegistry | None = None
) -> dict[str, Any]:
    """A JSON-serializable catalog: foundation tools, then a two-level hierarchy of
    financial **disciplines** -> subject **domains** (each with its tools + skills), and an
    extensions bucket (anything outside the domain taxonomy).

    Foundation = the always-on web + core utilities. Disciplines and the domains within
    them are ordered by the taxonomy's canonical order and carry display titles; items
    sort by name within a domain.
    """
    foundation: list[dict[str, Any]] = []
    by_domain: dict[str, dict[str, list]] = {}
    extensions: list[dict[str, Any]] = []

    for spec in [*registry.core_tools(), *registry.deferred_tools()]:
        family, domain = classify_tool(spec)
        entry = _tool_dict(spec, family, domain)
        if family in FOUNDATION_FAMILIES:
            foundation.append(entry)
        elif domain in DOMAIN_SPECS:
            by_domain.setdefault(domain, {"tools": [], "skills": []})["tools"].append(entry)
        else:  # a tool whose domain isn't in the taxonomy
            extensions.append(entry)

    for name in skill_registry.names() if skill_registry else []:
        sk = skill_registry.get(name)
        _, domain = classify_skill(sk)
        entry = _skill_dict(sk, domain)
        if domain in DOMAIN_SPECS:
            by_domain.setdefault(domain, {"tools": [], "skills": []})["skills"].append(entry)
        else:
            extensions.append(entry)

    def _domain_dict(key: str) -> dict[str, Any]:
        return {
            "key": key,
            "title": DOMAIN_SPECS[key].title,
            "order": DOMAIN_SPECS[key].order,
            "tools": sorted(by_domain[key]["tools"], key=lambda x: x["name"]),
            "skills": sorted(by_domain[key]["skills"], key=lambda x: x["name"]),
        }

    # Roll the present domains up under their disciplines; keep disciplines and domains in
    # canonical order, and drop a discipline that has no populated domains.
    by_discipline: dict[str, list[str]] = defaultdict(list)
    for key in by_domain:
        by_discipline[DOMAIN_SPECS[key].discipline].append(key)
    disciplines = [
        {
            "key": dkey,
            "title": DISCIPLINE_SPECS[dkey].title,
            "order": DISCIPLINE_SPECS[dkey].order,
            "domains": [
                _domain_dict(k)
                for k in sorted(by_discipline[dkey], key=lambda k: DOMAIN_SPECS[k].order)
            ],
        }
        for dkey in sorted(by_discipline, key=lambda d: DISCIPLINE_SPECS[d].order)
    ]
    return {
        "foundation": sorted(foundation, key=lambda x: x["name"]),
        "disciplines": disciplines,
        "extensions": sorted(extensions, key=lambda x: x["name"]),
    }
