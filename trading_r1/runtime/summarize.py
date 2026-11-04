# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Conversation summarization: the `/compact` capability.

One turn-agnostic operation: fold the accumulated history into a single LLM-written
snapshot and keep the *live exchange* (the latest question and the latest answer)
verbatim. It reads the same whether the session is one long research turn or twenty:
everything up to the latest answer is summarized (the bulky tool scaffolding included).
An optional caller instruction steers what to preserve (e.g. "keep every ticker figure
and table"). Fail-open: on any error, or when
there is nothing to fold (a bare question and answer, or a turn still in flight), the history
is returned unchanged.

Distinct from ``runtime.recovery.compact_messages``: that elides old tool-result
*bodies* automatically *within* a single run; this summarizes across the whole
*session*, on demand.
"""

from __future__ import annotations

from typing import Any

from trading_r1.providers import ModelProfile
from trading_r1.runtime.context_budget import estimate_tokens, window_of
from trading_r1.runtime.model_call import completion
from trading_r1.runtime.run_state import is_harness_note
from trading_r1.state.runtime_config import RuntimeConfig, load_runtime_config

_TOOL_BUDGET = 1200  # chars of each tool result shown to the summarizer (their bodies are long)
_CHARS_PER_WINDOW_TOKEN = 2  # the summarizer reads at most half its window, the newest kept

SUMMARY_PROMPT = """\
You compact a finance research conversation so it fits the model's context window \
while losing nothing that matters, keeping each `[N]` source number and `e` record id as \
written. Write a faithful structured snapshot another \
analyst could continue from, not a vague paraphrase.

Cover, as applicable:
- The question(s) and the goal/intent.
- Entities in scope (tickers, companies, sector).
- Key figures established: preserve exact numbers, tickers, dates, and units \
verbatim; never round or invent.
- Tools used and their notable results, with the evidence ids (`e<n>`) they carry; \
the records stay with the session, so the ids let the figures be read back exactly.
- Conclusions reached, the current state, and any open next steps.

Return the snapshot as concise markdown. Report only what the conversation actually \
established; do not add new facts."""


def _user_indices(messages: list[dict[str, Any]]) -> list[int]:
    return [i for i, m in enumerate(messages) if m.get("role") == "user" and not is_harness_note(m)]


def _final_answer_index(messages: list[dict[str, Any]]) -> int | None:
    """Index of the settled last reply, or ``None`` if the turn isn't complete. A turn is complete
    only when its last message, past any harness notes, is an assistant message with content and
    no pending tool calls: checking the tail avoids mistaking an intermediate note for it while
    a tool round is still in flight."""
    end = len(messages)
    while end and is_harness_note(messages[end - 1]):
        end -= 1
    if not end:
        return None
    m = messages[end - 1]
    content = m.get("content")
    settled = (
        m.get("role") == "assistant"
        and isinstance(content, str)  # structured/non-str content: not a settled text answer
        and content.strip()
        and not m.get("tool_calls")
    )
    return end - 1 if settled else None


def _compaction_plan(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """The one turn-agnostic plan. Returns ``(to_fold, tail)`` such that
    ``new = [snapshot(to_fold)] + tail``: fold the whole session up to the latest answer
    into one snapshot, keep the latest question and answer verbatim. The same rule holds
    for one turn or many: turn count never enters it, only the latest question, the latest
    answer, and everything before them.

    A harness note after the latest answer folds too: it may hold a long unfinished draft, and
    it is what a compaction may need to shrink. Only notes, with no exchange, fold whole.

    Returns ``(None, messages)`` for a no-op: the turn is still in flight (no settled
    answer), or there is nothing to fold beyond the latest exchange (a bare
    question and answer with no scaffolding or earlier turns)."""
    if messages and all(is_harness_note(m) for m in messages):
        return messages, []
    ans = _final_answer_index(messages)
    if ans is None:
        return None, messages
    users = _user_indices(messages)
    after = messages[ans + 1 :]
    if not users:
        return (messages, []) if after else (None, messages)
    to_fold = [*messages[:ans], *after]  # in order (a coherent summary)
    if len(to_fold) <= 1:  # only the latest question precedes the answer: nothing to fold
        return None, messages
    return to_fold, [messages[users[-1]], messages[ans]]  # latest question + answer, verbatim


def _render(messages: list[dict[str, Any]], budget: int) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content and m.get("tool_calls"):
            calls = ", ".join(tc["function"]["name"] for tc in m["tool_calls"])
            content = f"(calls: {calls})"
        if content:
            shown = content[:_TOOL_BUDGET] if role == "tool" else content
            lines.append(f"{role}: {shown}")
    return "\n".join(lines)[-budget:]


async def summarize_history(
    messages: list[dict[str, Any]],
    *,
    instruction: str | None = None,
    profile: ModelProfile,
    client: Any | None = None,
    config: RuntimeConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compact a session: fold the history so far into one snapshot, keeping the latest
    question and answer verbatim. Returns ``(new_messages, stats)``. Fail-open: returns
    the input unchanged on a no-op or any summarization error."""
    cfg = config or load_runtime_config()
    try:
        # Everything runs inside the try (planning, rendering, and the model call), so a
        # malformed stored message (a non-str content, a bad tool_call) fails open
        # (compacted=false) and never raises out of /compact.
        to_fold, tail = _compaction_plan(messages)
        if to_fold is None:
            reason = (
                "nothing to fold beyond the latest exchange"
                if _final_answer_index(messages) is not None
                else "the current turn is still running"
            )
            return messages, {"compacted": False, "reason": reason}
        extra = f"\n\nCaller instruction: {instruction.strip()}" if instruction else ""
        budget = window_of(profile, cfg.recovery) * _CHARS_PER_WINDOW_TOKEN
        user = f"Conversation so far:\n\n{_render(to_fold, budget)}{extra}"
        resp = await completion(
            cfg,
            profile,
            [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": user}],
            client=client,
        )
        if resp.finish_reason == "length":  # a cut-off summary would drop what was cut
            return messages, {"compacted": False, "reason": "the summary was cut off"}
        summary = resp.text.strip()
    except Exception:  # noqa: BLE001 (fail-open: never lose the session on a compaction error)
        return messages, {"compacted": False, "reason": "compaction failed"}
    if not summary:
        return messages, {"compacted": False, "reason": "empty summary"}

    snapshot = {
        "role": "assistant",
        "content": f"[Earlier conversation: compacted summary]\n\n{summary}",
    }
    new = [snapshot, *tail]
    return new, {
        "compacted": True,
        "messages_before": len(messages),
        "messages_after": len(new),
        "tokens_before": estimate_tokens(messages),
        "tokens_after": estimate_tokens(new),
        "summary_chars": len(summary),
    }
