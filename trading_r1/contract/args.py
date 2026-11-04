# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The tool-argument contract.

A tool declares its inputs as a Pydantic model, wrapped here so the registry, ``load_tool``
and the dispatcher hold one contract: emit a JSON Schema, validate a mapping.

Validation raises one :class:`ArgsValidationError`: the dispatcher's never-raise guarantee
rests on catching one failure type, so bad arguments surface as a normal ``ok=False`` result
rather than escaping the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class ArgsIssue:
    """One validation failure: where, what, and which rule (Pydantic's error type), so the
    model gets an actionable, path-aware message."""

    loc: str
    msg: str
    kind: str = ""


class ArgsValidationError(Exception):
    """Arguments did not satisfy their schema."""

    def __init__(self, issues: list[ArgsIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i.loc}: {i.msg}" for i in issues) or "invalid arguments")


_VALUES = frozenset({"default", "const", "enum", "examples"})  # data, not schema


def _without_titles(schema: Any) -> Any:
    """A JSON Schema without Pydantic's generated ``title`` keys: no model reads them. A field
    named ``title`` has a schema as its value, so it stays."""
    if isinstance(schema, list):
        return [_without_titles(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: value if key in _VALUES else _without_titles(value)
        for key, value in schema.items()
        if not (key == "title" and isinstance(value, str))
    }


class Args:
    """A tool's arguments: a Pydantic v2 model, whose validation yields a model instance."""

    def __init__(self, model: type[BaseModel]) -> None:
        self.model = model
        self._schema: dict[str, Any] | None = None

    def json_schema(self) -> dict[str, Any]:
        """The model's JSON Schema, built once; callers treat it as read-only."""
        if self._schema is None:
            self._schema = _without_titles(self.model.model_json_schema())
            self._schema.pop("description", None)  # the tool's own description says it
        return self._schema

    def validate(self, data: dict[str, Any]) -> BaseModel:
        try:
            return self.model.model_validate(data)
        except ValidationError as err:
            issues = [
                ArgsIssue(
                    loc=".".join(str(p) for p in i.get("loc", ())) or "(root)",
                    msg=i.get("msg", "invalid"),
                    kind=i.get("type", ""),
                )
                for i in err.errors()
            ]
            raise ArgsValidationError(issues) from err
