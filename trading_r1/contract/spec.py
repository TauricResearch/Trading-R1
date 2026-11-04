# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The tool spec and what a handler returns.

A tool is a :class:`ToolSpec`: a name, a result kind, a request model, an output model and an async
handler. The handler returns a :class:`Result` (the output model and its :class:`Provenance`)
or raises :class:`ToolError` when it cannot deliver. The dispatcher stores the output's JSON
as the evidence payload and renders the model's text from it (``contract/render.py``).

Naming: a dotted display path (``data.equity.reference``) maps to an underscored wire name
(``data_equity_reference``) because the OpenAI function-name regex forbids dots.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from trading_r1.contract.args import Args
from trading_r1.contract.render import render

Tier = Literal["core", "deferred"]
# data: facts fetched from a provider; compute: figures derived from inputs; web: pages and
# search; control: the harness's own moves (loading, planning, asking, memory)
ResultKind = Literal["data", "compute", "web", "control"]
ToolHandler = Callable[[Any], Awaitable["Result"]]

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
MIN_DESCRIPTION_CHARS = 30


class ToolError(Exception):
    """An expected, actionable failure: no usable data, the provider is unavailable, or the
    input cannot be served. The dispatcher returns the message verbatim as an ``ok=False``
    result; the message says what to change. An unexpected bug surfaces as
    ``handler_exception`` instead. A failure a data API answered also carries its ``code`` and
    ``retry`` (``later``, ``never`` or ``change_request``), for a caller that tells an outage
    from an absence."""

    def __init__(self, message: str, *, code: str | None = None, retry: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry = retry


class Output(BaseModel):
    """The base of every output model: fields typed with :mod:`~trading_r1.contract.units`,
    declared in the order the model should read them. The renderer labels each value by its
    path, so a field's name is what the model chains (``prev:e4.ttm.ebitda``)."""

    model_config = ConfigDict(extra="forbid")


def rendered(
    *, index: str | None = None, then: str | None = None, rows: int | None = None
) -> ConfigDict:
    """An output's config with rendering hints. ``index`` names the field its arrays (list
    fields, or mappings of lists, of the same length) align to: the renderer shows them as
    one table, ``rows`` of it before head and tail stand in. ``then`` is the next step the
    tool suggests, written after the result."""
    pairs = (("x-index", index), ("x-then", then), ("x-rows", rows))
    extra = {k: v for k, v in pairs if v}
    return ConfigDict(extra="forbid", json_schema_extra=extra)


class Series(Output):
    """An output whose arrays align to its oldest-first ``dates`` field, which the subclass
    declares where the table belongs in the reading order."""

    model_config = rendered(index="dates")


@functools.cache
def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """A model's JSON Schema, computed once per class."""
    return model.model_json_schema()


def hint(
    *, hidden: bool = False, rows: int | None = None, axes: tuple[str, str] | None = None
) -> FieldInfo:
    """Rendering hints for a field, as data in its schema: ``hidden`` keeps it out of the text
    (it stays in the payload and chains); ``rows`` sets how many rows of a table are shown;
    ``axes`` names the sibling lists a matrix (a list of rows) is laid out against, rows then
    columns: one labelled table, the axes not repeated."""
    extra: dict[str, Any] = {}
    if hidden:
        extra["x-hidden"] = True
    if rows is not None:
        extra["x-rows"] = rows
    if axes is not None:
        extra["x-axes"] = list(axes)
    return Field(json_schema_extra=extra)


class Provenance(BaseModel):
    """Where a result comes from, kept with its evidence and shown in the rail: the publisher
    of the data (``source``), what it is about (``subject``,
    ``view``) as the tool resolved the request, the date its data runs to (``as_of``) and the
    method of a computation."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    subject: str | None = None
    view: str | None = None
    as_of: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class Result:
    """A handler's return: the output and its provenance."""

    output: Output
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def payload(self) -> dict[str, Any]:
        """The output as JSON: the evidence payload, what ``prev:`` resolves against."""
        # validated again: an output changed after construction meets its model here too
        return type(self.output).model_validate(self.output.model_dump()).model_dump(mode="json")

    @property
    def text(self) -> str:
        """The output as the model reads it."""
        return render(self.payload, schema_of(type(self.output)))


_SENTENCE_END = re.compile(r"(?<!e\.g)(?<!i\.e)\. ")
_SUMMARY_CHARS = 170


def catalog_summary(description: str) -> str:
    """A description's first sentence; one longer than `_SUMMARY_CHARS` stops at its first colon
    or dash outside brackets (a lead-in to a list) or, lacking one, before the bracket or word
    it would cut. The catalog line says what a tool or skill is for, and the model loads the
    rest when it needs it."""
    first = _SENTENCE_END.split(description.strip())[0].rstrip(".")
    if len(first) > _SUMMARY_CHARS:
        head = first[:_SUMMARY_CHARS]
        cuts = [
            i
            for sep in (": ", " — ")
            if 0 < (i := first.find(sep)) <= _SUMMARY_CHARS
            and first[:i].count("(") == first[:i].count(")")
        ]
        if cuts:
            first = first[: min(cuts)]
        else:
            if head.count("(") > head.count(")"):
                first = head[: head.rindex("(")].rstrip(" ,;:—")
            else:
                first = head.rsplit(" ", 1)[0].rstrip(" ,;:—") + " …"
    return first + "."


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool's contract.

    name:           wire id (underscores, OpenAI-regex-safe)
    display_name:   dotted path for the human-readable catalog
    result_kind:    what kind of result it gives (``ResultKind``); consumers branch on it, never
                    on the name
    tier:           "core" (always visible) | "deferred" (catalog-only)
    description:    catalog routing text (>= 30 chars)
    request_schema: the Pydantic model of the arguments
    handler:        async callable receiving the validated request
    output:         the output model a handler's :class:`Result` carries
    tags:           freeform routing hints surfaced in the catalog
    evidence:       whether a successful call is recorded as evidence: true for anything that
                    produces facts (data, compute, research); false for most controls
    inline_refs:    ``(text_field, values_field)`` when ``prev:`` references may sit inside
                    the text (a calc expression): each becomes a named entry of the values
                    field, resolved like any reference (runtime/chaining.py ``embed_references``)

    ``args`` is the normalized view of ``request_schema``; ``output_schema`` is the output
    model's JSON Schema (MCP's ``outputSchema``), which the renderer reads.
    """

    name: str
    display_name: str
    result_kind: ResultKind
    tier: Tier
    description: str
    request_schema: type[BaseModel]
    handler: ToolHandler
    output: type[Output]
    tags: tuple[str, ...] = field(default_factory=tuple)
    evidence: bool = True
    inline_refs: tuple[str, str] | None = None
    args: Args = field(init=False, compare=False, repr=False)
    output_schema: dict[str, Any] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", Args(self.request_schema))
        object.__setattr__(self, "output_schema", schema_of(self.output))
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"Tool name {self.name!r} violates {_NAME_RE.pattern}: "
                f"use underscores not dots on the wire."
            )
        if len(self.description) < MIN_DESCRIPTION_CHARS:
            raise ValueError(
                f"Tool description for {self.name} is {len(self.description)} chars; "
                f"minimum {MIN_DESCRIPTION_CHARS} (catalog routing needs signal)."
            )

    def openai_schema(self) -> dict[str, Any]:
        """OpenAI function-calling format, passed via the ``tools=`` parameter."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args.json_schema(),
            },
        }

    def catalog_line(self) -> str:
        """One-line catalog entry for the deferred-tool block in the prompt: what the tool is
        for and the arguments it cannot run without. Its full description and argument schema
        arrive with `load_tool`."""
        required = self.request_schema.model_json_schema().get("required") or []
        needs = f", needs {', '.join(required)}" if required else ""
        return f"- {self.display_name} (`{self.name}`{needs}): {catalog_summary(self.description)}"
