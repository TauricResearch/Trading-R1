# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""ask_user: the model asks the human a clarifying question mid-run (suspend-and-resume).

A core tool. When the request is genuinely ambiguous in a way that would send the work in
materially different directions, the model calls ``ask_user`` with 1-3 structured
multiple-choice questions; the call blocks while the client renders them and the user
answers, then the answers return as the tool result and the run continues with full context
(see :mod:`trading_r1.runtime.elicitation`). A non-interactive run auto-resolves to the
recommended (first) option, so a question never stalls a headless run.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Text
from trading_r1.runtime.elicitation import Elicitor


class AskOption(BaseModel):
    """One answer choice."""

    label: str = Field(..., min_length=1, description="A short answer choice.")
    description: str = Field("", description="Optional one-line gloss on the choice.")


class AskQuestion(BaseModel):
    """One multiple-choice question to put to the user."""

    question: str = Field(..., min_length=1, description="One clear question (a single sentence).")
    header: str = Field(
        ..., min_length=1, max_length=16, description="A <=16-char chip labeling the topic."
    )
    options: list[AskOption] = Field(
        ...,
        min_length=2,
        max_length=4,
        description="2-4 mutually-exclusive choices; put the recommended one first. A "
        "free-form 'Other' is added by the client: don't include one.",
    )


class AskUserRequest(BaseModel):
    """Input for asking the user one to three clarifying questions."""

    questions: list[AskQuestion] = Field(
        ..., min_length=1, max_length=3, description="1-3 questions to ask together."
    )


_DESCRIPTION = (
    "Ask the user 1-3 short multiple-choice questions and wait for their answers, then "
    "continue with them in mind. Use ONLY when the request is genuinely ambiguous in a way "
    "that would change the work materially (unclear scope, a consequential choice); "
    "strongly prefer proceeding on sensible defaults over asking, and ask early, before the "
    "bulk of the work. Each question needs a short "
    "header and 2-4 options with the recommended one first. If no one is available to answer "
    "(e.g. a scripted run), it resolves to the recommended option so the run never stalls."
)


class Answer(BaseModel):
    question: Text
    answer: Text  # an option, or the user's own words


class AskUserOutput(Output):
    """The user's answers; ``note`` when none came back."""

    answers: list[Answer]
    note: Text = None


_NO_ANSWER = (
    "No answer was provided. Proceed on your best judgment and state the assumption you made."
)


def build_ask_user_spec(elicitor: Elicitor) -> ToolSpec:
    """Build the per-run ``ask_user`` tool bound to this run's elicitor (emit + timeout)."""

    async def handler(req: AskUserRequest) -> Result:
        # Index-based ids let the client pair answers back to questions unambiguously.
        questions = [{"id": str(i), **q.model_dump()} for i, q in enumerate(req.questions)]
        answers = [
            Answer(question=str(a.get("question", "")), answer=str(a["answer"]))
            for a in await elicitor.ask(questions)
            if a.get("answer")
        ]
        return Result(AskUserOutput(answers=answers, note=None if answers else _NO_ANSWER))

    return ToolSpec(
        name="ask_user",
        display_name="core.ask_user",
        result_kind="control",
        tier="core",
        description=_DESCRIPTION,
        request_schema=AskUserRequest,
        handler=handler,
        output=AskUserOutput,
        tags=("core", "meta"),
        evidence=False,
    )
