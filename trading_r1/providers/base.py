# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Provider abstraction: a uniform turn interface over heterogeneous backbones.

Each backbone speaks a different wire protocol (OpenAI-compatible Chat Completions,
the OpenAI Responses API, Gemini's native SDK, the Anthropic Messages API). A
:class:`Provider` hides that: it maps the normalized chat history to its native request, issues
the call (streaming reasoning/content deltas via ``on_delta`` when one is given), and returns a
single :class:`AssistantTurn`. The loop, recovery, and history threading are written once
against these normalized types and never touch a provider's native shape.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from trading_r1.providers.profiles import ModelProfile

# on_delta(kind, text) with kind in {"reasoning", "content"}: the streaming seam.
DeltaCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class ToolCall:
    """A normalized tool call. ``arguments`` is the raw JSON string the model
    emitted (parsed at dispatch). ``extra`` carries opaque provider state that
    must round-trip (e.g. Gemini's ``thought_signature``), echoed back verbatim
    on the next turn or multi-turn tool calling fails."""

    id: str
    name: str
    arguments: str
    extra: dict[str, Any] | None = None


def tool_arguments(raw: str | None) -> dict[str, Any]:
    """A tool call's JSON argument string as a dict: a malformed or non-object string is ``{}``
    (the dispatcher reports the parse error itself)."""
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def function_schemas(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Chat-Completions tool schemas as flat ``{name, description, parameters}`` dicts, which
    each native protocol then wraps in its own tool shape."""
    out: list[dict[str, Any]] = []
    for t in tools or []:
        fn = t.get("function", t)
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def tag_native(profile: ModelProfile, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Provider-native turn items, marked with the protocol and model that wrote them: another
    protocol or model cannot read them, and a provider replays only its own."""
    return {"protocol": profile.provider, "model": profile.model, "items": items}


def native_items(profile: ModelProfile, message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The native items on an assistant message if this profile wrote them, else None (the
    message is then rebuilt from its content and tool calls)."""
    native = message.get("_native")
    if (
        isinstance(native, dict)
        and native.get("protocol") == profile.provider
        and native.get("model") == profile.model
    ):
        return native.get("items") or None
    return None


@dataclass(frozen=True)
class Completion:
    """The result of a structured completion: the text and why generation stopped."""

    text: str
    finish_reason: str = "stop"


@dataclass
class AssistantTurn:
    """One assistant turn, normalized across providers: the visible content, any tool
    calls, and why generation stopped. (Reasoning is surfaced live via the on_delta
    streaming seam, not carried on the turn.)"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    # Opaque, provider-native turn items threaded back verbatim on the next request,
    # e.g. the OpenAI Responses reasoning items (encrypted) that must round-trip
    # statelessly. Tagged with the protocol and model that wrote it (`tag_native`) and carried in
    # the history under "_native"; only that provider reads it (the wire layer strips
    # "_"-prefixed keys for everyone else).
    native: dict[str, Any] | None = None

    @property
    def is_actionable(self) -> bool:
        """True when the turn carries something the loop can act on: tool calls to
        dispatch, or content to answer with. A turn with neither is a failed
        generation (e.g. the model emitted tool-call syntax the parser could not
        extract, leaving the text in the reasoning channel), not an empty answer."""
        return bool(self.tool_calls) or bool((self.content or "").strip())

    def to_message(self) -> dict[str, Any]:
        """Render this turn as a chat-history message for the next request."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                    **({"extra_content": tc.extra} if tc.extra else {}),
                }
                for tc in self.tool_calls
            ]
        if self.native is not None:
            msg["_native"] = self.native
        return msg


class Provider(ABC):
    """A model backbone behind the normalized turn interface. One subclass per wire protocol
    (`ModelProfile.provider` names it); it also answers what depends on the protocol: how to
    list its models, what its context window is and what its context-overflow error says."""

    # Lower-case fragments of the 400 message this protocol's endpoint returns for a prompt
    # that exceeds the model's window.
    overflow_signatures: ClassVar[tuple[str, ...]] = ()

    def __init__(self, profile: ModelProfile, client: Any | None = None) -> None:
        self.profile = profile
        self.client = client

    def models_request(self) -> tuple[str, dict[str, str]]:
        """The URL and headers that list the models the endpoint serves (the readiness probe).
        The default speaks the OpenAI-compatible ``/models`` with a bearer key."""
        from trading_r1.providers.client import client_for

        key = self.profile.resolve_api_key()
        headers = {"Authorization": f"Bearer {key}"} if key and key != "EMPTY" else {}
        return str(client_for(self.profile).base_url).rstrip("/") + "/models", headers

    def is_overflow(self, message: str) -> bool:
        """Whether a 400's message says the prompt exceeds the context window."""
        text = message.lower()
        return any(sig in text for sig in self.overflow_signatures)

    async def context_window(self) -> int | None:
        """The context window the server reports for the profile's model, or None when it
        reports none. May raise; the caller treats a failure as no answer."""
        return None

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        on_delta: DeltaCallback | None = None,
    ) -> AssistantTurn:
        """Issue one turn. Streams deltas via ``on_delta`` when given (else a single
        non-streaming call), and returns the assembled :class:`AssistantTurn`. Tool
        choice is always the provider default (``auto``)."""
        ...

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> Completion:
        """One non-streaming completion with no tools, its reply constrained to
        ``response_schema`` (a JSON Schema) when given: the call behind the page reader, the
        and summaries. The default speaks OpenAI-compatible Chat Completions, which
        every provider but a native-protocol one offers; a native provider overrides it."""
        from trading_r1.providers.client import complete

        cc = await complete(
            self.profile,
            messages,
            max_tokens=max_tokens,
            response_schema=response_schema,
            client=self.client,
        )
        if not cc.choices:
            return Completion("", "error")
        choice = cc.choices[0]
        return Completion(
            choice.message.content or "", getattr(choice, "finish_reason", None) or "stop"
        )
