# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Minimal YAML-frontmatter parsing for named spec files: skills (SKILL.md).

A spec is ``---\\n<yaml>\\n---\\n<body>``. This splits and validates the frontmatter, and
holds the checks every named spec shares: a kebab-case name, a description long enough to
route on, a non-empty body.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from trading_r1.contract.spec import MIN_DESCRIPTION_CHARS

NAME_RE = re.compile(r"^[a-z][a-z0-9-]+$")
# The frontmatter block: a `---` fence line, the YAML, a closing `---` fence line. Fences are
# whole lines, so a `---` inside a value or the body never ends the block.
_FENCED = re.compile(r"---[ \t]*\n(.*?)^---[ \t]*$\n?(.*)", re.DOTALL | re.MULTILINE)


class FrontmatterError(ValueError):
    """A malformed spec file."""


def split_frontmatter(text: str, *, source: str | None = None) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter_dict, body)``. Raises FrontmatterError if the file
    lacks a leading ``---`` fenced YAML block."""
    where = f" ({source})" if source else ""
    if not text.lstrip().startswith("---"):
        raise FrontmatterError(f"spec{where} must start with a '---' frontmatter block")
    m = _FENCED.match(text.lstrip())
    if not m:
        raise FrontmatterError(f"spec{where} frontmatter block is not closed with '---'")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"spec{where} has invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise FrontmatterError(f"spec{where} frontmatter must be a mapping")
    return fm, m.group(2).strip()


def as_str_list(fm: dict[str, Any], key: str) -> tuple[str, ...]:
    """Coerce a frontmatter value to a tuple of strings ([] when absent)."""
    v = fm.get(key)
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    if isinstance(v, list):
        return tuple(str(x) for x in v)
    raise FrontmatterError(f"frontmatter `{key}` must be a string or list")


def named_fields(fm: dict[str, Any], allowed: frozenset[str], kind: str) -> tuple[str, str]:
    """A named spec's ``(name, description)``, after rejecting unknown frontmatter keys."""
    unknown = set(fm) - allowed
    if unknown:
        raise FrontmatterError(f"{kind} frontmatter has unknown keys: {sorted(unknown)}")
    name, description = fm.get("name"), fm.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise FrontmatterError(f"{kind} frontmatter needs string `name` and `description`")
    return name, description


def check_named(kind: str, name: str, description: str, body: str) -> None:
    """The checks every named spec shares."""
    if not NAME_RE.match(name):
        raise FrontmatterError(f"{kind} name {name!r} must be kebab-case")
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise FrontmatterError(f"{kind} {name} description < {MIN_DESCRIPTION_CHARS} chars")
    if not body.strip():
        raise FrontmatterError(f"{kind} {name} body is empty")
