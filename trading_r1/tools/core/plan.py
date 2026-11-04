# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""update_plan: a live research plan the model maintains for multi-step work.

The model resends the full ordered list of steps (each with a status) whenever the
plan changes; the tool emits a ``plan`` event so the UI can render a checklist that
advances as the run proceeds. Pure and stateless: the model owns the plan, this broadcasts it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from trading_r1.contract import events
from trading_r1.contract.events import PlanStatus
from trading_r1.contract.spec import Output, Result, ToolSpec
from trading_r1.contract.units import Count
from trading_r1.runtime.tool_events import emit_tool_event


class PlanStep(BaseModel):
    """One item in the live research plan."""

    step: str = Field(..., description="A short, specific, actionable step (imperative phrase).")
    status: PlanStatus = Field("pending", description="One of: pending, in_progress, completed.")


class UpdatePlanRequest(BaseModel):
    """Input for replacing the live plan with its latest full state."""

    steps: list[PlanStep] = Field(
        ...,
        min_length=1,
        description="The full ordered plan as a list of {step, status} objects: resend "
        "every step (with its current status) on each update, e.g. "
        '[{"step": "Pull NVDA fundamentals", "status": "completed"}, '
        '{"step": "Run a DCF", "status": "in_progress"}, '
        '{"step": "Write the report", "status": "pending"}].',
    )


class PlanOutput(Output):
    """The plan as recorded: how many steps, how many done (the steps go to the UI)."""

    steps: Count
    completed: Count


async def _handler(req: UpdatePlanRequest) -> Result:
    emit_tool_event(
        events.Plan(items=[events.PlanItem(step=s.step, status=s.status) for s in req.steps])
    )
    done = sum(1 for s in req.steps if s.status == "completed")
    return Result(
        PlanOutput(steps=len(req.steps), completed=done),
    )


_DESCRIPTION = (
    "Maintain a short plan for a multi-step task (3+ steps): call this with the full "
    "ordered list of steps, each marked pending / in_progress / completed. Keep exactly "
    "one step in_progress, and update as you finish steps. In the terminal the user sees "
    "it as a checklist. Skip it for trivial one- or two-step questions."
)

SPEC = ToolSpec(
    name="update_plan",
    display_name="core.update_plan",
    result_kind="control",
    tier="core",
    description=_DESCRIPTION,
    request_schema=UpdatePlanRequest,
    handler=_handler,
    output=PlanOutput,
    tags=("core", "meta"),
    evidence=False,
)
