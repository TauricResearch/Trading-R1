# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Tool dispatch: the never-raise bridge from a tool_call to a tool_result.

Every tool invocation flows through :func:`dispatch`: coerce args -> resolve
reference chaining -> validate against the tool's schema -> run the handler -> check its
:class:`Result` against the declared output, store the payload as evidence and render it. It
**always** returns a :class:`DispatchResult` and never raises; every error becomes an
actionable ``ok=False`` text the model self-corrects from on the next turn.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from trading_r1.contract.args import ArgsValidationError
from trading_r1.contract.render import render
from trading_r1.contract.spec import Result, ToolError, ToolSpec
from trading_r1.runtime.arg_coercion import coerce_args
from trading_r1.runtime.chaining import (
    embed_references,
    format_chain_errors,
    non_numeric,
    referenced_ids,
    resolve_references,
)
from trading_r1.runtime.session import recorder
from trading_r1.runtime.sources import source_batch
from trading_r1.runtime.tool_events import ToolEmit, tool_event_scope
from trading_r1.runtime.tool_registry import ToolRegistry, ToolSessionState

_MAX_VALIDATION_ISSUES = 5


def _header(spec: ToolSpec, provenance: dict[str, Any], evidence_id: str | None) -> str:
    """The first line of a rendered result: its evidence id (what ``prev:`` names), the tool
    and what the result is about."""
    about = ", ".join(
        str(provenance[k]) for k in ("subject", "view") if provenance.get(k) is not None
    )
    when = f" / as of {provenance['as_of']}" if provenance.get("as_of") else ""
    call = f"{spec.display_name}({about}){when}" if about else f"{spec.display_name}{when}"
    return f"[{evidence_id}] {call} / prev:{evidence_id}.<path>" if evidence_id else call


def _shape(spec: ToolSpec, response: Result) -> tuple[str, dict, dict]:
    """The model's text, the payload and the meta of a handler's return. A :class:`Result`
    must carry the spec's declared output model; its JSON is the payload."""
    if not isinstance(response, Result):
        raise TypeError(f"{spec.name} returned {type(response).__name__}, not a Result")
    if not isinstance(response.output, spec.output):
        raise TypeError(
            f"{spec.name} returned {type(response.output).__name__}, "
            f"not its declared {spec.output.__name__}"
        )
    payload = response.payload
    return (
        render(payload, spec.output_schema),
        payload,
        response.provenance.model_dump(exclude_none=True),
    )


def _format_args_error(err: ArgsValidationError) -> str:
    """Compact, path-aware validation message the model can act on: one rendering for
    both Pydantic-backed and JSON-Schema-backed tools."""
    issues = err.issues
    lines = [
        f"  {i.loc}: {i.msg}" + (f" [{i.kind}]" if i.kind else "")
        for i in issues[:_MAX_VALIDATION_ISSUES]
    ]
    if len(issues) > _MAX_VALIDATION_ISSUES:
        lines.append(f"  …and {len(issues) - _MAX_VALIDATION_ISSUES} more")
    return "schema validation failed:\n" + "\n".join(lines)


class DispatchResult:
    """The model-facing text plus the trajectory side: payload, provenance, evidence id, and
    the tool's ``result_kind`` (None for a call that never reached a tool), which consumers branch
    on rather than the tool's name."""

    def __init__(
        self,
        *,
        name: str,
        args: dict[str, Any],
        ok: bool,
        text: str,
        payload: dict[str, Any],
        meta: dict[str, Any],
        body: str | None = None,
        evidence_id: str | None = None,
        pointer: str | None = None,
        result_kind: str | None = None,
    ) -> None:
        self.name = name
        self.result_kind = result_kind
        self.args = args
        self.ok = ok
        self.text = text  # what the model reads
        self.body = text if body is None else body  # the tool's own part of it, for the trace
        self.payload = payload
        self.meta = meta
        self.evidence_id = evidence_id
        self.pointer = pointer

    def for_model(self) -> str:
        """The tool_result content the model sees."""
        return self.text

    def to_message(self, tool_call_id: str) -> dict[str, Any]:
        """The tool message for the context. An evidence result carries its record's pointer
        (``_pointer``, never sent to a provider): what the message becomes when cut."""
        msg = {"role": "tool", "tool_call_id": tool_call_id, "content": self.for_model()}
        return {**msg, "_pointer": self.pointer} if self.pointer else msg

    def to_log(self) -> dict[str, Any]:
        """The trajectory tool_log entry: the call as an audit artifact. An evidence result's
        payload lives once, in the evidence (``evidence`` names it); a failed call or a control
        tool keeps its ``payload`` here."""
        entry = {
            "name": self.name,
            "args": self.args,
            "ok": self.ok,
            "text_chars": len(self.text),
            "meta": self.meta,
        }
        if self.evidence_id:
            return {**entry, "evidence": self.evidence_id}
        return {**entry, "payload": self.payload}


def _error_result(
    name: str, args: dict[str, Any], text: str, error: str, **meta: Any
) -> DispatchResult:
    return DispatchResult(
        name=name,
        args=args,
        ok=False,
        text=text,
        payload={"error": error},
        meta=meta,
    )


async def _recorded(
    name: str,
    args: dict[str, Any],
    call_id: str | None,
    run: Callable[[], Awaitable[DispatchResult]],
) -> DispatchResult:
    """``run()`` as a call in the session record, when one is bound: a ``tool.call`` line before,
    a ``tool.result`` after, also when the call is cancelled (status ``interrupted``)."""
    rec = recorder.current()
    if rec is None:
        return await run()
    call = call_id or f"c{uuid.uuid4().hex[:8]}"
    event = rec.tool_call(name, call, args)
    try:
        with recorder.tool_scope(call, event):
            result = await run()
    except BaseException:
        rec.tool_result(call, event, "interrupted")
        raise
    if not result.ok:
        error = {"code": str(result.payload.get("error", "")), "message": result.text}
        rec.tool_result(call, event, "error", error=error)
    elif result.evidence_id:
        rec.tool_result(call, event, "ok", artifact=result.evidence_id)
    else:
        rec.tool_result(call, event, "ok", result=result.text)
    return result


async def dispatch(
    name: str,
    args: dict[str, Any],
    *,
    registry: ToolRegistry,
    session_state: ToolSessionState | None = None,
    call_id: str | None = None,
    emit: ToolEmit | None = None,
    record: bool = True,
) -> DispatchResult:
    """Execute a registered tool by name. Always returns a DispatchResult.

    ``session_state`` drives reference chaining and records the result as evidence (``record``
    false for a call made inside another tool, whose own result is the evidence); ``call_id``
    (the provider's) scopes the tool's progress events. The call and its result are written to
    the session record when one is bound (``runtime/session/recorder.py``), except for a call made
    inside another tool.
    """

    async def run() -> DispatchResult:
        return await _dispatch(
            name,
            args,
            registry=registry,
            session_state=session_state,
            call_id=call_id,
            emit=emit,
            record=record,
        )

    if not record:
        return await run()
    return await _recorded(name, args, call_id, run)


async def _dispatch(
    name: str,
    args: dict[str, Any],
    *,
    registry: ToolRegistry,
    session_state: ToolSessionState | None,
    call_id: str | None,
    emit: ToolEmit | None,
    record: bool,
) -> DispatchResult:
    spec = registry.get(name)
    if spec is None:
        return _error_result(
            name,
            args,
            (
                f"Unknown tool: '{name}'. Available core tools: "
                f"{sorted(s.name for s in registry.core_tools())}. "
                f"For deferred tools, call load_tool with the names you need first."
            ),
            "unknown_tool",
        )

    # From here on report the tool's canonical name, not the spelling the model happened to
    # use. `registry.get` accepts the dotted display form as well as the wire id, so without
    # this one tool would appear in the trajectory
    # under two different names depending on how the model wrote it.
    name = spec.name

    # 1. Argument coercion (schema-driven; repairs stringified containers and
    #    wrapped lone refs). Runs before validation.
    coerced = coerce_args(args, spec.args)
    lifted: dict[str, str] = {}
    if spec.inline_refs and session_state is not None:
        coerced, lifted = embed_references(coerced, *spec.inline_refs)

    # 2. Reference-chaining: resolve prev:<call_id>.<path> against prior
    #    structured payloads, before validation, so the schema sees real values.
    if session_state is not None:
        resolved, chain_errors = resolve_references(coerced, session_state.evidence.payloads)
        if lifted and not chain_errors:
            chain_errors = non_numeric(resolved[spec.inline_refs[1]], lifted)
        if chain_errors:
            return DispatchResult(
                name=name,
                args=args,
                ok=False,
                text=format_chain_errors(chain_errors),
                payload={
                    "error": "chain_resolution",
                    "failures": [
                        {"reference": e.reference, "reason": e.reason} for e in chain_errors
                    ],
                },
                meta={},
            )
    else:
        resolved = coerced

    # 3. Schema validation: one error type, so bad args can't escape the never-raise path.
    try:
        validated = spec.args.validate(resolved)
    except ArgsValidationError as err:
        return _error_result(name, args, _format_args_error(err), "schema_validation")

    # 4. Handler execution (the handler owns provider-specific retry; the
    #    dispatcher is the never-raise backstop).
    t0 = time.time()
    try:
        with tool_event_scope(emit, name, call_id), source_batch() as cited:
            response = await spec.handler(validated)
        # Shape the success result inside the guard too: a handler that returns the wrong
        # type without raising surfaces as a normal ok=False result, never an escape.
        text, payload, meta = _shape(spec, response)
        meta.setdefault("elapsed_ms", int((time.time() - t0) * 1000))
        # Record the result as evidence, so it resolves by id from here on, this turn or a
        # later one, whether or not this message stays in the context.
        rec = None
        if session_state is not None and spec.evidence and record:
            rec = session_state.evidence.add(
                tool=name,
                args=args,
                payload=payload,
                meta=meta,
                output=text,
                inputs=referenced_ids(coerced),
                run=session_state.run,
            )
        evidence_id = rec.id if rec else None
        for bibliography, index in cited if rec else ():
            bibliography.link(index, rec.id)  # a source is the record that produced it
        return DispatchResult(
            name=name,
            args=args,
            ok=True,
            # a control's text stands alone; a fact opens with where it comes from
            text=(
                text
                if spec.result_kind == "control"
                else f"{_header(spec, meta, evidence_id)}\n{text}"
            ),
            result_kind=spec.result_kind,
            body=text,
            payload=payload,
            meta=meta,
            evidence_id=evidence_id,
            pointer=rec.pointer() if rec else None,
        )
    except ValidationError as err:
        return _error_result(
            name,
            args,
            f"tool handler returned an invalid response: {err}",
            "handler_response_invalid",
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    except ToolError as err:  # expected, actionable failure: clean message, no prefix
        return _error_result(
            name,
            args,
            str(err),
            "tool_error",
            elapsed_ms=int((time.time() - t0) * 1000),
        )
    except Exception as err:  # noqa: BLE001 (never-raise: surface to the model)
        return _error_result(
            name,
            args,
            f"{type(err).__name__}: {err}",
            "handler_exception",
            exception=type(err).__name__,
            elapsed_ms=int((time.time() - t0) * 1000),
        )


async def dispatch_json_args(
    name: str,
    args_json: str,
    *,
    registry: ToolRegistry,
    session_state: ToolSessionState | None = None,
    call_id: str | None = None,
    emit: ToolEmit | None = None,
) -> DispatchResult:
    """Dispatch a tool whose args arrive as a JSON string (the form the
    function-calling parser produces)."""
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as err:
        raw, why = {"_raw": args_json}, str(err)

        async def failed() -> DispatchResult:
            return _error_result(
                name, raw, f"could not parse arguments as JSON: {why}", "args_not_json"
            )

        return await _recorded(name, raw, call_id, failed)
    return await dispatch(
        name, args, registry=registry, session_state=session_state, call_id=call_id, emit=emit
    )
