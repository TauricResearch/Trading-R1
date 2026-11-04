# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The OpenAI Responses API provider.

GPT reasoning models surface their reasoning only via the Responses API: Chat
Completions returns none and 400s on ``reasoning_effort`` alongside tools. This
provider speaks Responses natively: it streams reasoning summaries + output text,
calls function tools, and threads reasoning state **statelessly**: the prior
turn's reasoning / function-call items round-trip as input items (``store=False``),
so no server-side conversation state is needed.

The normalized chat history is translated to Responses ``input`` items on every
call; an assistant turn this provider produced carries its native output items
under ``_native`` (see :meth:`AssistantTurn.to_message`) and they are re-emitted
verbatim, which keeps the encrypted reasoning chain intact across tool calls.
"""

from __future__ import annotations

from typing import Any

from trading_r1.providers.base import (
    AssistantTurn,
    DeltaCallback,
    Provider,
    ToolCall,
    function_schemas,
    native_items,
    tag_native,
)
from trading_r1.providers.client import client_for
from trading_r1.providers.profiles import ModelProfile
from trading_r1.providers.usage import record_openai

# Ask for the encrypted reasoning so it can be threaded back across tool calls.
_INCLUDE = ["reasoning.encrypted_content"]
# Server-set / SDK-convenience fields that the API rejects when an item is fed back
# as input; stripped before threading.
_NON_INPUT_FIELDS = ("parsed_arguments", "status")


def _to_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Chat-Completions tool schemas -> Responses function tools (flat, not nested)."""
    return [{"type": "function", **fn, "strict": False} for fn in function_schemas(tools)]


def _to_input(profile: ModelProfile, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalized chat history -> Responses ``input`` items.

    An assistant turn this profile wrote carries ``_native`` and re-emits its provider items
    verbatim (the reasoning chain); otherwise it is reconstructed from content + tool_calls
    (e.g. seeded history or a turn from another provider or model)."""
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "developer", "user"):
            items.append({"role": role, "content": m.get("content") or ""})
        elif role == "assistant":
            native = native_items(profile, m)
            if native:
                items.extend(native)
                continue
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc["id"],
                        "name": fn["name"],
                        "arguments": fn["arguments"],
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m["tool_call_id"],
                    "output": m.get("content") or "",
                }
            )
    return items


def _turn_from_response(profile: ModelProfile, resp: Any) -> AssistantTurn:
    """Normalize a Responses ``Response`` into an AssistantTurn (+ native items)."""
    output = list(getattr(resp, "output", None) or [])
    calls: list[ToolCall] = []
    for it in output:
        if it.type == "function_call":
            calls.append(ToolCall(id=it.call_id, name=it.name, arguments=it.arguments))
    text = getattr(resp, "output_text", "") or ""
    if calls:
        finish = "tool_calls"
    elif getattr(resp, "status", None) == "incomplete" and (
        getattr(getattr(resp, "incomplete_details", None), "reason", None) == "max_output_tokens"
    ):
        finish = "length"
    elif not output and not text:
        # no output at all (e.g. a refusal/blocked response): signal failure so the
        # loop's recovery treats it like the chat provider's empty-choices case.
        finish = "error"
    else:
        finish = "stop"
    native = [
        {k: v for k, v in it.model_dump(exclude_none=True).items() if k not in _NON_INPUT_FIELDS}
        for it in output
    ]
    return AssistantTurn(
        content=text,
        tool_calls=calls,
        finish_reason=finish,
        native=tag_native(profile, native),
    )


class OpenAIProvider(Provider):
    """OpenAI Responses API backbone: reasoning summaries + stateless threading."""

    # a 400 with code `context_length_exceeded`
    overflow_signatures = ("exceeds the context window", "context_length_exceeded")

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        on_delta: DeltaCallback | None = None,
    ) -> AssistantTurn:
        cl = self.client or client_for(self.profile)
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "input": _to_input(self.profile, messages),
            "reasoning": {"effort": "high", "summary": "detailed"},
            "include": _INCLUDE,
            "store": False,
            "max_output_tokens": max_tokens,
        }
        responses_tools = _to_tools(tools)
        if responses_tools:
            kwargs["tools"] = responses_tools

        if on_delta is None:
            resp = await cl.responses.create(**kwargs)
        else:
            async with cl.responses.stream(**kwargs) as stream:
                async for ev in stream:
                    if ev.type == "response.reasoning_summary_text.delta":
                        on_delta("reasoning", ev.delta)
                    elif ev.type == "response.output_text.delta":
                        on_delta("content", ev.delta)
                resp = await stream.get_final_response()
        record_openai(self.profile.name, resp)
        return _turn_from_response(self.profile, resp)
