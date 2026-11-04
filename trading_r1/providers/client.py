# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The backbone client seam: build a client and issue chat completions.

One place constructs the OpenAI-compatible ``AsyncOpenAI`` client and assembles
the create-kwargs (model-agnostic base + profile ``extra_body`` + quirk adapter).
Both a non-streaming :func:`complete` and a streaming :func:`stream` are provided;
the streaming path returns an assembled ``ChatCompletion`` of the *same shape* as
the non-streaming one, so every downstream consumer is identical either way.

The ``client`` argument is injectable so the seam is unit-testable without a
network (pass a fake exposing ``chat.completions.create`` / ``.stream``).
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from trading_r1.providers.adapters import apply_adapter
from trading_r1.providers.base import DeltaCallback
from trading_r1.providers.profiles import ModelProfile
from trading_r1.providers.usage import record_openai


def tool_call_extra(tc: Any) -> Any:
    """The provider's ``extra_content`` on a tool call, whether the SDK surfaced it as a
    real attribute or parked it in ``model_extra``. Gemini rides a ``thought_signature``
    here that must round-trip or multi-turn tool calling 400s; absent for other providers."""
    return getattr(tc, "extra_content", None) or (getattr(tc, "model_extra", None) or {}).get(
        "extra_content"
    )


def client_for(profile: ModelProfile) -> AsyncOpenAI:
    """Construct the async OpenAI-compatible client for a profile. A keyless
    vLLM endpoint gets the conventional ``"EMPTY"`` placeholder. The SDK's own retries are
    off: the runtime's one recovery policy (`runtime.model_call`) retries."""
    return AsyncOpenAI(
        api_key=profile.resolve_api_key() or "EMPTY",
        base_url=profile.base_url,
        timeout=profile.timeout_s,
        max_retries=0,
    )


def build_create_kwargs(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Assemble the chat-completion kwargs for a profile.

    Vanilla OpenAI params first (so any compatible endpoint works), then the
    profile's ``extra_body``, then the quirk adapter. ``tools`` are omitted
    entirely when empty (endpoints treat that as "no tool calling").

    ``response_schema`` (a JSON Schema) constrains the reply to that shape via
    ``response_format``, schema-enforced structured output.
    A single wire field the OpenAI-compatible endpoints (OpenAI, vLLM, Gemini's compat
    layer) share, so it needs no per-provider branch."""
    g = profile.generation
    # Strip "_"-prefixed history keys (opaque provider state, e.g. another provider's
    # "_native" threading items): they're not valid wire fields for Chat Completions.
    messages = [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": messages,
        "temperature": g.temperature,
        "top_p": g.top_p,
        "max_tokens": max_tokens,
        "presence_penalty": g.presence_penalty,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": response_schema, "strict": True},
        }
    if stream:
        # the final chunk then carries the token counts (an adapter may drop this for an
        # endpoint that rejects it)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    if profile.extra_body:
        kwargs["extra_body"] = dict(profile.extra_body)
    return apply_adapter(profile.adapter, kwargs)


async def complete(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    response_schema: dict[str, Any] | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatCompletion:
    """Issue one non-streaming chat completion and return it."""
    cl = client or client_for(profile)
    kwargs = build_create_kwargs(
        profile,
        messages,
        tools=tools,
        max_tokens=max_tokens,
        response_schema=response_schema,
    )
    cc = await cl.chat.completions.create(**kwargs)
    record_openai(profile.name, cc)
    return cc


async def stream(
    profile: ModelProfile,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    on_delta: DeltaCallback | None = None,
    client: AsyncOpenAI | None = None,
) -> ChatCompletion:
    """Stream one completion, surfacing reasoning/content deltas via ``on_delta``,
    and return the assembled ``ChatCompletion`` (same shape as :func:`complete`).

    Uses the raw ``create(stream=True)`` iterator: the SDK's beta ``.stream()``
    auto-parse helper rejects non-``strict`` function tools, and these tools aren't
    strict. Accumulates content / reasoning
    / tool-call deltas ourselves and rebuild a ``ChatCompletion`` so every
    downstream consumer (and the recovery layer's ``length`` check) is identical
    to the non-streaming path."""
    cl = client or client_for(profile)
    kwargs = build_create_kwargs(profile, messages, tools=tools, max_tokens=max_tokens, stream=True)

    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    resp_id = "stream"
    model_name = profile.model
    created = 0
    last_usage = None  # the stream's final chunk, with no choices, carries the counts

    async for chunk in await cl.chat.completions.create(**kwargs):
        resp_id = chunk.id or resp_id
        model_name = chunk.model or model_name
        created = chunk.created or created
        if getattr(chunk, "usage", None):
            last_usage = chunk
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish_reason = choice.finish_reason
        delta = choice.delta
        reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
        if reasoning and on_delta is not None:
            on_delta("reasoning", reasoning)
        if delta.content:
            content_parts.append(delta.content)
            if on_delta is not None:
                on_delta("content", delta.content)
        for tc in delta.tool_calls or []:
            acc = tool_calls.setdefault(
                tc.index, {"id": "", "name": "", "arguments": "", "extra_content": None}
            )
            if tc.id:
                acc["id"] = tc.id
            if tc.function and tc.function.name:
                acc["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                acc["arguments"] += tc.function.arguments
            # Capture the tool-call extra_content (Gemini thought_signature) off the first
            # delta that carries it, to echo back next turn; see tool_call_extra.
            if extra := tool_call_extra(tc):
                acc["extra_content"] = extra

    record_openai(profile.name, last_usage)
    return _assemble_completion(
        resp_id=resp_id,
        model=model_name,
        created=created,
        content="".join(content_parts),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


def _assemble_completion(
    *,
    resp_id: str,
    model: str,
    created: int,
    content: str,
    tool_calls: dict[int, dict[str, Any]],
    finish_reason: str | None,
) -> ChatCompletion:
    """Rebuild a ``ChatCompletion`` from accumulated streaming deltas: the same
    shape ``complete()`` returns, so the loop, recovery, and logging are agnostic
    to whether the call streamed."""
    calls = [
        ChatCompletionMessageToolCall(
            id=acc["id"] or f"call_{idx}",
            type="function",
            function=Function(name=acc["name"], arguments=acc["arguments"]),
            # preserve the provider's tool-call extra_content (Gemini thought_signature);
            # the SDK model allows extras, so it lands in model_extra for echo-back.
            **({"extra_content": acc["extra_content"]} if acc.get("extra_content") else {}),
        )
        for idx, acc in sorted(tool_calls.items())
    ]
    message = ChatCompletionMessage(
        role="assistant",
        content=content or None,
        tool_calls=calls or None,
    )
    choice = Choice(
        index=0,
        message=message,
        # default to "stop" only if the stream ended without a reason
        finish_reason=finish_reason or "stop",  # type: ignore[arg-type]
    )
    return ChatCompletion(
        id=resp_id,
        choices=[choice],
        created=created,
        model=model,
        object="chat.completion",
    )
