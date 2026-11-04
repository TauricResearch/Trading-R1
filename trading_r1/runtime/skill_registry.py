# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Skill registry: catalog + load-on-demand for workflow recipes.

A skill is `skills/<name>/SKILL.md`: YAML frontmatter (name == dir, description,
tags, requires_tools) + a markdown body the model follows as a recipe. Parallel
to the tool registry: catalog in the prompt, `load_skill` pulls the body and
auto-loads the skill's `requires_tools`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from trading_r1.contract.spec import catalog_summary
from trading_r1.runtime import frontmatter as fm_lib

_log = logging.getLogger(__name__)

_ALLOWED_KEYS = frozenset({"name", "description", "tags", "requires_tools", "domain"})


@dataclass(frozen=True)
class SkillSpec:
    """A parsed skill recipe: catalog metadata, required tools, and markdown body."""

    name: str
    description: str
    body: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    requires_tools: tuple[str, ...] = field(default_factory=tuple)
    domain: str = ""  # subject domain (taxonomy): groups the skill with its tools
    source_path: str | None = None

    def __post_init__(self) -> None:
        fm_lib.check_named("skill", self.name, self.description, self.body)

    def catalog_line(self) -> str:
        return f"- `{self.name}`: {catalog_summary(self.description)}"


def parse_skill_md(text: str, *, source_path: str | None = None) -> SkillSpec:
    """Parse one SKILL.md document into a validated :class:`SkillSpec`."""

    fm, body = fm_lib.split_frontmatter(text, source=source_path)
    name, description = fm_lib.named_fields(fm, _ALLOWED_KEYS, "skill")
    domain = fm.get("domain")
    return SkillSpec(
        name=name,
        description=description,
        body=body,
        tags=fm_lib.as_str_list(fm, "tags"),
        requires_tools=fm_lib.as_str_list(fm, "requires_tools"),
        domain=domain if isinstance(domain, str) else "",
        source_path=source_path,
    )


def load_skill_dir(skill_dir: Path) -> SkillSpec:
    """Load and validate ``skill_dir/SKILL.md``."""

    md = skill_dir / "SKILL.md"
    if not md.is_file():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")
    spec = parse_skill_md(md.read_text(encoding="utf-8"), source_path=str(md))
    if spec.name != skill_dir.name:
        raise fm_lib.FrontmatterError(f"skill name {spec.name!r} != directory {skill_dir.name!r}")
    return spec


class SkillRegistry:
    """In-memory catalog of workflow skills available to a run."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec, *, override: bool = False) -> None:
        if spec.name in self._skills and not override:
            raise ValueError(f"Skill {spec.name} already registered")
        self._skills[spec.name] = spec

    def get(self, name: str) -> SkillSpec | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def all(self) -> list[SkillSpec]:
        return list(self._skills.values())


@dataclass
class SkillSessionState:
    """Per-run skill state: the recipes already loaded for this conversation."""

    loaded: set[str] = field(default_factory=set)


def load_skill_registry(skills_root: Path) -> SkillRegistry:
    """Register every `skills/<name>/SKILL.md` under ``skills_root`` (strict: raises
    on a malformed skill; used for the bundled first-party skills)."""
    reg = SkillRegistry()
    merge_skills(reg, skills_root, strict=True)
    return reg


def merge_skills(reg: SkillRegistry, skills_root: Path, *, strict: bool = False) -> int:
    """Merge `<name>/SKILL.md` skills from ``skills_root`` into ``reg`` (override by
    name; later roots win). Returns the count merged. With ``strict=False`` a
    malformed user skill is skipped, not fatal: a dropped-in skill can't break a run."""
    if not skills_root.is_dir():
        return 0
    n = 0
    for d in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if not (d / "SKILL.md").is_file():
            continue
        try:
            reg.register(load_skill_dir(d), override=True)
            n += 1
        except Exception as err:  # noqa: BLE001
            if strict:
                raise
            _log.warning("skipped skill %s: %s", d, err)
            continue  # best-effort for user/project skills
    return n
