# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The OpenAI-compatible Chat Completions provider.

The native interface for a vLLM server, and the path any OpenAI-compatible
endpoint speaks. Wraps the :mod:`client` seam (which builds the request, applies the
quirk adapter, and assembles streamed deltas into a ``ChatCompletion``) and
normalizes the result into an :class:`AssistantTurn`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from openai.types.chat import ChatCompletion

from trading_r1.providers.base import AssistantTurn, DeltaCallback, Provider, ToolCall
from trading_r1.providers.client import complete, stream, tool_call_extra


def turn_from_completion(cc: ChatCompletion) -> AssistantTurn:
    """Normalize a ``ChatCompletion`` (streamed or not) into an AssistantTurn."""
    if not cc.choices:
        return AssistantTurn(finish_reason="error")
    choice = cc.choices[0]
    msg = choice.message
    calls: list[ToolCall] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        calls.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments,
                extra=tool_call_extra(tc) or None,
            )
        )
    return AssistantTurn(
        content=msg.content or "",
        tool_calls=calls,
        finish_reason=choice.finish_reason or "stop",
    )


_WINDOW_TIMEOUT_S = 3.0


class ChatCompletionsProvider(Provider):
    """vLLM / OpenAI-compatible Chat Completions backbone."""

    overflow_signatures = (
        "maximum context length",
        "reduce the length",
        "longer than the maximum",
        "context length",
    )

    async def context_window(self) -> int | None:
        """A vLLM server states ``max_model_len`` on the model's ``/models`` entry; an Ollama
        server states ``<architecture>.context_length`` in ``/api/show``. A hosted
        OpenAI-compatible API states neither."""
        profile = self.profile
        if not profile.base_url:
            return None
        async with httpx.AsyncClient(timeout=_WINDOW_TIMEOUT_S) as client:
            url, headers = self.models_request()
            resp = await client.get(url, headers=headers)
            for entry in resp.json().get("data", []):
                if entry.get("id") == profile.model and isinstance(entry.get("max_model_len"), int):
                    return int(entry["max_model_len"])
            parts = urlsplit(profile.base_url)
            resp = await client.post(
                f"{parts.scheme}://{parts.netloc}/api/show", json={"model": profile.model}
            )
            for key, value in resp.json().get("model_info", {}).items():
                if key.endswith(".context_length") and isinstance(value, int):
                    return value
        return None

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int,
        on_delta: DeltaCallback | None = None,
    ) -> AssistantTurn:
        kwargs: dict[str, Any] = {
            "tools": tools,
            "max_tokens": max_tokens,
            "client": self.client,
        }
        if on_delta is None:
            cc = await complete(self.profile, messages, **kwargs)
        else:
            cc = await stream(self.profile, messages, on_delta=on_delta, **kwargs)
        return turn_from_completion(cc)
