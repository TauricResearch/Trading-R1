# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Execution modes: each a system-prompt variant over the same full tool surface.
Mode is a request field selecting the prompt; the tool registry is the same for every mode
(web tools visible, data and compute tools deferred but callable, `load_tool`). Keeping the
toolset constant means switching mode mid-session never strands the model with history that
references a tool the new mode lacks.

  auto        general default; the agent decides how to research.
  research    web-first deep research; research prompt.
  analytical  numbers-first: lead with data/compute tools (compute, not in head).

The prompt sets the emphasis; the deferred tier keeps the visible schema lean per
mode without removing tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_r1.runtime.taxonomy import DOMAINS, FOUNDATION_FAMILIES

DEFAULT_MODE = "auto"


@dataclass(frozen=True)
class ModeSpec:
    """Execution-mode metadata: a *declarative* profile over the capability taxonomy.

    A mode never gates the registry (everything stays loadable in every mode; mid-session
    switching is safe). It only declares (a) which guidance prose to use and (b) which domain packs
    are *foregrounded* (shown expanded) vs backgrounded (collapsed to names + a load cue) in the
    catalog. The
    foundation (web/core) is always foregrounded; presentation is advisory, not a limit.
    """

    name: str
    prompt_variant: str  # key into prompts.PROMPT_VARIANTS (the guidance prose)
    description: str
    foreground_domains: frozenset[str] = field(default_factory=frozenset)  # auto = DOMAINS (all)

    def foregrounds(self, family: str, domain: str | None) -> bool:
        """Is a capability of this ``(family, domain)`` foregrounded in this mode?"""
        if family in FOUNDATION_FAMILIES:
            return True  # foundation (web/core) is always shown
        return domain is not None and domain in self.foreground_domains


MODES: dict[str, ModeSpec] = {
    # Canonical superset: the agent decides; everything expanded.
    "auto": ModeSpec(
        "auto",
        "auto",
        description="Full toolkit; the agent decides.",
        foreground_domains=DOMAINS,
    ),
    # Web-first: foreground only equity data/skills (facts to ground the story); the quant
    # packs stay loadable but collapse to names.
    "research": ModeSpec(
        "research",
        "research",
        description="Web-first deep research (search, visit, cite).",
        foreground_domains=frozenset({"equity"}),
    ),
    # Numbers-first: foreground every data/compute domain.
    "analytical": ModeSpec(
        "analytical",
        "analytical",
        description="Numbers-first: data + computation tools.",
        foreground_domains=DOMAINS,
    ),
}


def get_mode(name: str | None) -> ModeSpec:
    """Resolve a mode name to its spec; unknown or missing gives the default mode."""
    return MODES.get((name or "").strip().lower(), MODES[DEFAULT_MODE])


def resolve_mode(name: str | None) -> str:
    """Effective mode name: an explicit ``name`` (normalized; unknown gives the default mode), else
    the default mode. The single source the ``run_start`` frame and the agent's prompt variant
    both use, so the advertised mode always matches the one that runs."""
    return get_mode(name).name
