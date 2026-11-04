# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Tool registry: registration and per-run session state.

The tool contract itself is ``trading_r1.contract.spec``. **Core** tools are always visible
to the model; **deferred** tools appear only in a catalog until ``load_tool`` pulls their
schema (the two-tier disclosure pattern). The registry stores wire names.

Registries are explicit objects passed to dispatch: fresh per request, no global mutable
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_r1.contract.spec import ToolSpec
from trading_r1.runtime.evidence import EvidenceStore


class ToolRegistry:
    """A set of tool specs. Created fresh per request (no global state)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name} already registered")
        self._tools[spec.name] = spec

    def clone(self) -> ToolRegistry:
        """A shallow copy: same specs, independent dict. Lets a run add per-run
        meta-tools without mutating a shared base registry."""
        new = ToolRegistry()
        new._tools = dict(self._tools)
        return new

    def get(self, name: str) -> ToolSpec | None:
        """Look up by wire name; fall back to the underscored variant when the
        model emits the dotted display form (a known Qwen quirk). Collision-safe:
        no registered name contains a dot."""
        spec = self._tools.get(name)
        if spec is None and "." in name:
            spec = self._tools.get(name.replace(".", "_"))
        return spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def core_tools(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.tier == "core"]

    def deferred_tools(self) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.tier == "deferred"]


@dataclass
class ToolSessionState:
    """Per-run state: which deferred tools have been loaded, and the evidence store this run
    records into and resolves ``prev:`` against: the session's. ``run`` labels the records
    this run writes."""

    loaded_deferred: set[str] = field(default_factory=set)
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    run: str = ""

    def load(self, names: list[str], registry: ToolRegistry) -> tuple[list[ToolSpec], list[str]]:
        """Mark deferred names as loaded; return ``(loaded specs, unknown names)``.
        Best-effort, mirroring ``load_skill``: valid names load even if others are
        unknown (the caller reports the unknown ones so the model self-corrects).
        Core names are silently OK."""
        loaded: list[ToolSpec] = []
        unknown: list[str] = []
        for n in names:
            spec = registry.get(n)
            if spec is None:
                unknown.append(n)
                continue
            if spec.tier == "deferred":
                self.loaded_deferred.add(spec.name)
            loaded.append(spec)
        return loaded, unknown

    def visible_schemas(self, registry: ToolRegistry) -> list[dict[str, Any]]:
        """Schemas the model can call now: every core tool + every loaded
        deferred tool."""
        schemas = [spec.openai_schema() for spec in registry.core_tools()]
        for name in sorted(self.loaded_deferred):
            spec = registry.get(name)
            if spec is not None:
                schemas.append(spec.openai_schema())
        return schemas
