# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What every channel shares: the channel interface, a send's receipt, and the declaration of
a kind of channel (the settings it needs, how they are checked).

A kind declares its settings as typed fields. Everything that asks for them (the command line,
the terminal's forms, the service's API) reads the declaration, and validation is one function
over it, so a new kind is a class and a declaration and nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from trading_r1.delivery.message import Message
from trading_r1.state import credentials

Status = Literal["sent", "failed", "unknown"]
_TEXT_MAX = 300
_PATH_MAX = 1024


@dataclass(frozen=True)
class Receipt:
    status: Status
    detail: str = ""
    ids: tuple[int, ...] = ()  # what the channel calls the parts that went out, where it names them


class ChannelError(ValueError):
    """A channel's settings are missing or invalid, so it cannot be built or used."""


class Channel(ABC):
    name: str

    @abstractmethod
    def address(self) -> str:
        """Where a message goes, as recorded with it (never a secret)."""

    @abstractmethod
    async def send(self, message: Message) -> Receipt: ...


@dataclass(frozen=True)
class Field:
    """One setting of a kind. ``secret`` holds the *name* of a credential (see ``credentials``),
    never the value; ``default`` is the name offered."""

    name: str
    label: str
    type: Literal["text", "int", "choice", "list", "secret", "path"]
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    help: str = ""
    example: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "minimum": self.minimum,
            "maximum": self.maximum,
            "help": self.help,
            "example": self.example,
        }


def _one_line(value: Any, limit: int) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or len(value) > limit:
        raise ValueError(f"one line of text, up to {limit} characters")
    return value.strip()


def _coerce(f: Field, value: Any) -> Any:
    if f.type == "text":
        return _one_line(value, _TEXT_MAX)
    if f.type == "path":
        return _one_line(value, _PATH_MAX)
    if f.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("a whole number")
        if (f.minimum is not None and value < f.minimum) or (
            f.maximum is not None and value > f.maximum
        ):
            raise ValueError(f"between {f.minimum} and {f.maximum}")
        return value
    if f.type == "choice":
        if value not in f.choices:
            raise ValueError("one of " + ", ".join(f.choices))
        return value
    if f.type == "secret":
        if not isinstance(value, str) or not credentials.valid_name(value):
            raise ValueError("a name in capitals, digits and underscores, e.g. MY_KEY")
        return value
    if not isinstance(value, list) or not value:
        raise ValueError("a list with at least one entry")
    return [_one_line(v, _TEXT_MAX) for v in value]


@dataclass(frozen=True)
class Kind:
    """A kind of channel: how it is described to the owner and how one is built."""

    name: str
    label: str
    summary: str
    fields: tuple[Field, ...]
    make: Callable[[Mapping[str, Any], Mapping[str, str]], Channel]
    presets: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    check: Callable[[dict[str, Any]], list[str]] | None = None  # rules across fields

    def validate(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """The settings, checked strictly: known fields only, required ones present, each of its
        type. Raises ``ChannelError`` naming every problem."""
        known = {f.name: f for f in self.fields}
        problems = [f"{k}: not a setting of {self.name}" for k in values if k not in known]
        out: dict[str, Any] = {}
        for f in self.fields:
            value = values.get(f.name, f.default)
            if value is None or value == "" or value == []:
                if f.required:
                    problems.append(f"{f.name}: required")
                continue
            try:
                coerced = _coerce(f, value)
            except ValueError as err:
                problems.append(f"{f.name}: {err}")
                continue
            if coerced == "" or (isinstance(coerced, list) and "" in coerced):
                if f.required or isinstance(coerced, list):
                    problems.append(f"{f.name}: {'required' if f.required else 'no blank entries'}")
                continue
            out[f.name] = coerced
        if not problems and self.check:
            problems += self.check(out)
        if problems:
            raise ChannelError("; ".join(problems))
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "summary": self.summary,
            "fields": [f.describe() for f in self.fields],
            "presets": {k: dict(v) for k, v in self.presets.items()},
        }

    def secret_fields(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.type == "secret")


__all__ = ["Channel", "ChannelError", "Field", "Kind", "Receipt", "Status"]
