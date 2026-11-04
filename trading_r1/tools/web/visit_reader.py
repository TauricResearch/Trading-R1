# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Reader-model extraction helpers for the `visit` tool.

The reader's output shape is enforced by the provider (``response_schema``): every backbone's
reader speaks the OpenAI-compatible Chat Completions API, which honours ``response_format``.
The prompt conveys only what the fields mean; the model cannot wrap the JSON in prose or omit a
field.
"""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from trading_r1.providers import ModelProfile
from trading_r1.runtime.model_call import completion
from trading_r1.runtime.session.recorder import redact
from trading_r1.state.runtime_config import RuntimeConfig, runtime_config


class ReaderOutput(BaseModel):
    """The reader's reply: the provider constrains it to this shape and the reply is checked
    against it, so a provider that ignored the schema reads as an unread page."""

    model_config = ConfigDict(extra="forbid")

    accessible: StrictBool
    summary: str
    evidence: str


_READER_SCHEMA = ReaderOutput.model_json_schema()

EXTRACTOR_PROMPT = """\
You extract grounded content from a fetched webpage for a research agent, into these fields:
  "accessible": true if the text is real article/report content; false if it is a \
paywall, login/bot wall, cookie/consent page, error page, or essentially empty.
  "summary": a faithful, information-dense summary of the page's substantive content \
(figures, facts, claims), using the page's own numbers. Empty string if not accessible.
  "evidence": one or two short verbatim quotes that support the key facts. Empty string \
if not accessible.
Report only what the page actually states. If it carries no real content (paywall, \
bot-wall, error, or essentially empty), set accessible=false with empty summary/evidence."""


async def read_page(
    profile: ModelProfile,
    text: str,
    *,
    goal: str | None,
    client: AsyncOpenAI | None,
    config: RuntimeConfig,
) -> dict[str, Any]:
    """Run the reader-model extraction over page text."""
    snippet = text[: runtime_config().reader.char_budget]
    user = (f"Research goal: {goal}\n\n" if goal else "") + f"Webpage content:\n\n{snippet}"
    try:
        resp = await completion(
            config,
            profile,
            [{"role": "system", "content": EXTRACTOR_PROMPT}, {"role": "user", "content": user}],
            max_tokens=config.reader.max_tokens,
            response_schema=_READER_SCHEMA,
            client=client,
        )
        raw = resp.text
    except Exception as exc:  # noqa: BLE001  # reader/transport failure, not page inaccessibility
        return {
            "accessible": False,
            "summary": "",
            "evidence": "",
            "reader_error": redact(str(exc)),
        }

    # Fail *closed*: a reply that is not exactly the object (a provider ignored the schema, a
    # field is missing or mistyped) is an unread page, never apparent evidence.
    try:
        out = ReaderOutput.model_validate_json(raw)
    except ValidationError:
        return {"accessible": False, "summary": "", "evidence": "", "reader_error": "malformed"}
    return {
        "accessible": out.accessible,
        "summary": out.summary.strip(),
        "evidence": out.evidence.strip(),
    }
