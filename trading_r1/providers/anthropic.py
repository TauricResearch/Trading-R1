# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The Anthropic provider (the Messages API).

The normalized history is translated to Messages each call: system messages become the top-level
``system``; a turn's tool results go in one user message, ahead of any text; an assistant turn this
profile wrote is replayed as the content blocks it returned (thinking blocks with their signatures
among them), tagged in ``_native`` so only the model that wrote them reads them.

Thinking blocks are bound to the conversation that produced them. The request asks the API to drop
the blocks of a history that was edited (the runtime trims old tool results and summarizes) rather
than reject it: ``block_binding.prefix_mismatch_behavior = "drop_block"``, which the
``thinking-binding-controls`` beta carries. Reasoning streams as the ``reasoning`` delta
(``display: "summarized"``). Sampling parameters and a forced tool choice are not sent: the current
models reject both.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from trading_r1.providers.base import (
    AssistantTurn,
    Completion,
    DeltaCallback,
    Provider,
    ToolCall,
    function_schemas,
    native_items,
    tag_native,
    tool_arguments,
)
from trading_r1.providers.profiles import ModelProfile
from trading_r1.providers.usage import record

_BETAS = ["thinking-binding-controls-2026-08-01"]
_VERSION = "2023-06-01"
_FINISH = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}  # anything else (refusal, pause_turn) is a failed generation


def _build_client(profile: ModelProfile) -> anthropic.AsyncAnthropic:
    """The SDK's own retries are off: the runtime's one recovery policy retries."""
    return anthropic.AsyncAnthropic(
        # with an address of its own, never the SDK's fallback to the environment's Anthropic key
        api_key=profile.resolve_api_key() or ("EMPTY" if profile.base_url else None),
        base_url=profile.base_url,
        timeout=profile.timeout_s,
        max_retries=0,
    )


def _to_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Chat-Completions tool schemas -> Messages tools."""
    return [
        {"name": fn["name"], "description": fn["description"], "input_schema": fn["parameters"]}
        for fn in function_schemas(tools)
    ]


def _blocks(content: Any) -> list[dict[str, Any]]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _to_messages(
    profile: ModelProfile, messages: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Normalized chat history -> ``(system, messages)``. Consecutive user content (tool results,
    then the next instruction) is one message, tool results first, as the API requires."""
    system: list[str] = []
    out: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            merged = out[-1]["content"] + blocks
            if role == "user":  # tool results lead
                merged.sort(key=lambda b: b["type"] != "tool_result")
            out[-1]["content"] = merged
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            system.append(str(m.get("content") or ""))
        elif role == "user":
            add("user", _blocks(m.get("content") or ""))
        elif role == "assistant":
            native = native_items(profile, m)
            if native:
                add("assistant", native)
                continue
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": fn["name"],
                        "input": tool_arguments(fn["arguments"]),
                    }
                )
            add("assistant", blocks or [{"type": "text", "text": "(no reply)"}])
        elif role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m.get("content") or "",
                    }
                ],
            )
    return "\n\n".join(s for s in system if s), out


def _turn_from_message(profile: ModelProfile, msg: Any) -> AssistantTurn:
    """Normalize a Messages response into an AssistantTurn (+ native blocks)."""
    text = "".join(b.text for b in msg.content if b.type == "text")
    calls = [
        ToolCall(id=b.id, name=b.name, arguments=json.dumps(b.input))
        for b in msg.content
        if b.type == "tool_use"
    ]
    finish = _FINISH.get(msg.stop_reason or "", "error")
    if finish == "tool_calls" and not calls:
        finish = "error"
    if finish == "length":  # a tool call cut off by the limit has partial arguments: never run it
        calls = []
    return AssistantTurn(
        content=text,
        tool_calls=calls,
        finish_reason=finish,
        native=tag_native(profile, [b.model_dump(exclude_none=True) for b in msg.content]),
    )


def _record(profile: ModelProfile, msg: Any) -> None:
    usage = getattr(msg, "usage", None)
    prompt = (
        (getattr(usage, "input_tokens", None) or 0)
        + (getattr(usage, "cache_read_input_tokens", None) or 0)
        + (getattr(usage, "cache_creation_input_tokens", None) or 0)
    )
    record(profile.name, prompt, getattr(usage, "output_tokens", None))


class AnthropicProvider(Provider):
    """Claude over the Messages API: reasoning summaries, signed thinking blocks, cached prefix."""

    # a 400: "prompt is too long: N tokens > M maximum"
    overflow_signatures = ("prompt is too long",)

    def models_request(self) -> tuple[str, dict[str, str]]:
        base = (self.profile.base_url or "https://api.anthropic.com").rstrip("/")
        key = self.profile.resolve_api_key()
        return f"{base}/v1/models", {"x-api-key": key, "anthropic-version": _VERSION}

    async def context_window(self) -> int | None:
        """``max_input_tokens`` on the model record."""
        record_ = await (self.client or _build_client(self.profile)).models.retrieve(
            self.profile.model
        )
        return getattr(record_, "max_input_tokens", None)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        on_delta: DeltaCallback | None = None,
    ) -> AssistantTurn:
        cl = self.client or _build_client(self.profile)
        system, history = _to_messages(self.profile, messages)
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "max_tokens": max_tokens,
            "messages": history,
            "thinking": {
                "type": "adaptive",
                "display": "summarized",
                "block_binding": {"prefix_mismatch_behavior": "drop_block"},
            },
            "cache_control": {"type": "ephemeral"},  # the stable prefix is cached
            "betas": _BETAS,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _to_tools(tools)
        # Always streamed: a long answer must not hit the SDK's non-streaming time limit.
        async with cl.beta.messages.stream(**kwargs) as stream:
            async for event in stream:
                if on_delta is not None and event.type == "content_block_delta":
                    if event.delta.type == "thinking_delta":
                        on_delta("reasoning", event.delta.thinking)
                    elif event.delta.type == "text_delta":
                        on_delta("content", event.delta.text)
            final = await stream.get_final_message()
        _record(self.profile, final)
        return _turn_from_message(self.profile, final)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> Completion:
        cl = self.client or _build_client(self.profile)
        system, history = _to_messages(self.profile, messages)
        output: dict[str, Any] = {"effort": "low"}  # extraction and summaries need no deep thought
        if response_schema is not None:
            output["format"] = {"type": "json_schema", "schema": response_schema}
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "max_tokens": max_tokens,
            "messages": history,
            "output_config": output,
        }
        if system:
            kwargs["system"] = system
        async with cl.messages.stream(**kwargs) as stream:
            final = await stream.get_final_message()
        _record(self.profile, final)
        text = "".join(b.text for b in final.content if b.type == "text")
        return Completion(text, _FINISH.get(final.stop_reason or "", "error"))
