# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The native Gemini provider (google-genai SDK).

The native SDK gives structured thought parts (``Part.thought``), function calls, and the
``thought_signature`` (opaque bytes) that must be echoed on the function-call part
or multi-turn tool calling fails.

The normalized chat history is translated to ``Content``s each call. The only
state that must round-trip is the per-call ``thought_signature``, carried
(base64, JSON-safe) on :class:`ToolCall.extra`, so no
turn-level threading blob is needed; function results are matched back by name.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from google import genai
from google.genai import types

from trading_r1.providers.base import (
    AssistantTurn,
    DeltaCallback,
    Provider,
    ToolCall,
    function_schemas,
    tool_arguments,
)
from trading_r1.providers.profiles import ModelProfile
from trading_r1.providers.usage import record

# Gemini finish reasons that mean the turn was suppressed, not completed.
_BLOCKED_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"}
)


def _build_client(profile: ModelProfile) -> genai.Client:
    return genai.Client(
        api_key=profile.resolve_api_key() or None,
        http_options=types.HttpOptions(timeout=int(profile.timeout_s * 1000)),  # ms
    )


def _to_tools(tools: list[dict[str, Any]] | None) -> list[types.Tool] | None:
    """Chat-Completions tool schemas -> a Gemini Tool of function declarations."""
    decls = [
        types.FunctionDeclaration(
            name=fn["name"], description=fn["description"], parameters_json_schema=fn["parameters"]
        )
        for fn in function_schemas(tools)
    ]
    return [types.Tool(function_declarations=decls)] if decls else None


def _to_contents(messages: list[dict[str, Any]]) -> tuple[str | None, list[types.Content]]:
    """Normalized chat history -> (system_instruction, contents).

    System messages fold into the system instruction; function results merge into a
    single user turn; an assistant turn becomes a model turn whose function-call
    parts carry the echoed ``thought_signature``."""
    system: list[str] = []
    contents: list[types.Content] = []
    id_to_name: dict[str, str] = {}
    fn_results: list[types.Part] = []

    def flush_results() -> None:
        if fn_results:
            contents.append(types.Content(role="user", parts=list(fn_results)))
            fn_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system.append(m["content"])
            continue
        if role == "tool":
            name = id_to_name.get(m.get("tool_call_id", ""), "")
            fn_results.append(
                types.Part.from_function_response(
                    name=name, response={"result": m.get("content") or ""}
                )
            )
            continue
        flush_results()
        if role == "user":
            contents.append(
                types.Content(role="user", parts=[types.Part(text=m.get("content") or "")])
            )
        elif role == "assistant":
            parts: list[types.Part] = []
            if m.get("content"):
                parts.append(types.Part(text=m["content"]))
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                id_to_name[tc["id"]] = fn["name"]
                sig = (tc.get("extra_content") or {}).get("thought_signature")
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=fn["name"], args=tool_arguments(fn["arguments"])
                        ),
                        thought_signature=base64.b64decode(sig) if sig else None,
                    )
                )
            if parts:
                contents.append(types.Content(role="model", parts=parts))
    flush_results()
    return ("\n".join(system) or None, contents)


def _parts(resp: Any) -> list[types.Part]:
    candidates = getattr(resp, "candidates", None) or []
    content = getattr(candidates[0], "content", None) if candidates else None
    return getattr(content, "parts", None) or []


def _finish(resp: Any) -> Any:
    candidates = getattr(resp, "candidates", None) or []
    return getattr(candidates[0], "finish_reason", None) if candidates else None


class GeminiProvider(Provider):
    """Native google-genai backbone: structured thoughts + thought_signature threading."""

    # a 400 INVALID_ARGUMENT: "The input token count (N) exceeds the maximum number of tokens
    # allowed (M)."
    overflow_signatures = ("exceeds the maximum number of tokens", "input token count")

    async def context_window(self) -> int | None:
        """Gemini's ``input_token_limit`` on the model record."""
        record = await (self.client or _build_client(self.profile)).aio.models.get(
            model=self.profile.model
        )
        return record.input_token_limit

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        on_delta: DeltaCallback | None = None,
    ) -> AssistantTurn:
        cl = self.client or _build_client(self.profile)
        system, contents = _to_contents(messages)
        g = self.profile.generation
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=_to_tools(tools),
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            temperature=g.temperature,
            top_p=g.top_p,
            max_output_tokens=max_tokens,
            # We dispatch tools ourselves; never let the SDK auto-execute.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        content: list[str] = []
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        finish: Any = None
        meta: Any = None  # usage metadata; a stream's chunks carry it cumulatively

        def consume(part: types.Part) -> None:
            if part.function_call:
                fc = part.function_call
                sig = part.thought_signature
                calls.append(
                    ToolCall(
                        id=fc.id or f"call_{len(calls)}",
                        name=fc.name or "",
                        arguments=json.dumps(dict(fc.args or {})),
                        extra=(
                            {"thought_signature": base64.b64encode(sig).decode()} if sig else None
                        ),
                    )
                )
            elif part.text:
                if part.thought:
                    reasoning.append(part.text)
                    if on_delta is not None:
                        on_delta("reasoning", part.text)
                else:
                    content.append(part.text)
                    if on_delta is not None:
                        on_delta("content", part.text)

        if on_delta is None:
            resp = await cl.aio.models.generate_content(
                model=self.profile.model, contents=contents, config=config
            )
            for part in _parts(resp):
                consume(part)
            finish = _finish(resp)
            meta = getattr(resp, "usage_metadata", None)
        else:
            async for chunk in await cl.aio.models.generate_content_stream(
                model=self.profile.model, contents=contents, config=config
            ):
                for part in _parts(chunk):
                    consume(part)
                if _finish(chunk):
                    finish = _finish(chunk)
                meta = getattr(chunk, "usage_metadata", None) or meta

        record(
            self.profile.name,
            getattr(meta, "prompt_token_count", None),
            (getattr(meta, "candidates_token_count", None) or 0)
            + (getattr(meta, "thoughts_token_count", None) or 0),
        )
        fr = getattr(finish, "value", finish)
        if calls:
            finish_reason = "tool_calls"
        elif fr == "MAX_TOKENS":
            finish_reason = "length"
        elif fr in _BLOCKED_REASONS or (finish is None and not content and not reasoning):
            # safety/recitation block, or no candidates at all: signal failure so the
            # loop's recovery treats it like the chat provider's empty-choices case.
            finish_reason = "error"
        else:
            finish_reason = "stop"
        return AssistantTurn(
            content="".join(content),
            tool_calls=calls,
            finish_reason=finish_reason,
        )
