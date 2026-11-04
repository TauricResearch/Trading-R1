# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What a session records beyond the turn's own events: every model call and every tool call.

A turn's recorder (:class:`Recording`) is bound for the length of the turn with :func:`bind`;
the model caller and the dispatcher find it with :func:`current`, so no handle is threaded
through the layers. ``/compact`` and the automatic compaction before a turn bind one of their
own, without a turn. A second variable holds the tool call being dispatched
(:func:`tool_scope`): a model call made inside a tool (the page reader) names that call as its
parent.

The lines written, in the order a turn produces them::

    user.message
    model.input*          each distinct payload (``{hash, kind, value}``), once: the
                          instructions, the tool list and each input message
    model.request         provider, profile, model, instructions, input, tools, settings;
                          the instructions, the tools and each input are
                          ``{"ref": <model.input id>}``; ``complete: false`` and ``missing``
                          when a payload could not be written
    model.response        parent = the request; status, output, finish_reason, usage, error
    tool.call             call = the model's call id, parent = the response
    artifact              the evidence record the call made (parent = the tool call)
    tool.result           call = the same id, parent = the tool call; status, result or artifact

A request records the chat messages the runtime sent, with the provider, endpoint, model and
settings; the conversion a provider makes to its own protocol is not recorded. Credentials
are never recorded: requests are recorded as messages, never headers, and an error's text has
every configured key removed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextvars import ContextVar
from typing import Any

from trading_r1.runtime.session import records
from trading_r1.runtime.session.records import Entry

_LOG = logging.getLogger(__name__)

_current: ContextVar[Recording | None] = ContextVar("tr1_recording", default=None)
_tool: ContextVar[tuple[str, str] | None] = ContextVar("tr1_recording_tool", default=None)

INLINE_RESULT = 16 * 1024  # a tool result larger than this is stored as an artifact
_KEY_SHAPES = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,})")
# what travels with an address or a header: user:password@, ?key=…, api_key: …, Bearer …
_CARRIED = re.compile(
    r"(://)[^/\s:@]+:[^/\s@]+(?=@)"
    r"|((?:[?&](?:api[_-]?)?(?:key|token|secret|password)|\b(?:api[_-]?)?(?:token|secret|password))=)[^&\s]+"
    r"|(\bapi[_-]?key\s*[=:]\s*)[^&\s]+"
    r"|(\bBearer\s+)\S+",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"
# no address reaches a log that may be copied elsewhere: the user's model server may be private
_ADDRESS = re.compile(
    r"\b(?:https?|wss?)://[^\s'\"<>)]+"
    r"|\b(?:\d{1,3}(?:\.\d{1,3}){3}|[\w.-]*[A-Za-z][\w.-]*):\d{2,5}(?![\d:])"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}\b"
)


def current() -> Recording | None:
    """The recorder bound in this context, if any."""
    return _current.get()


@contextlib.contextmanager
def bind(recording: Recording | None) -> Iterator[None]:
    """Make ``recording`` the one model and tool calls are recorded to."""
    token = _current.set(recording)
    try:
        yield
    finally:
        _current.reset(token)


@contextlib.contextmanager
def tool_scope(call_id: str, event_id: str) -> Iterator[None]:
    """The tool call being dispatched: its call id and the id of its ``tool.call`` line."""
    token = _tool.set((call_id, event_id))
    try:
        yield
    finally:
        _tool.reset(token)


def current_tool_call() -> tuple[str, str] | None:
    """``(call id, tool.call line id)`` of the tool being dispatched in this context."""
    return _tool.get()


def secrets() -> set[str]:
    """The credentials configured for any provider (the environment and the credential store)."""
    from trading_r1.state import credentials

    return {
        v
        for k, v in {**os.environ, **credentials.overlay()}.items()
        if v and len(v) >= 8 and (k.endswith(("_API_KEY", "_TOKEN")) or "SECRET" in k)
    }


def redact(text: str) -> str:
    """``text`` without any configured key, anything shaped like one, or an address."""
    for secret in sorted(secrets(), key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    text = _ADDRESS.sub("[address]", _KEY_SHAPES.sub(_REDACTED, text))
    return _CARRIED.sub(lambda m: f"{next(g for g in m.groups() if g)}{_REDACTED}", text)


def failure_text(err: BaseException) -> str:
    """An error as one short line that is safe to show or send."""
    text = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
    return redact(text)[:300]


def _digest(kind: str, item: Any) -> str:
    text = json.dumps([kind, item], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def turn_output(turn: Any) -> list[dict[str, Any]]:
    """The items of a model turn: its text, then its tool calls."""
    out: list[dict[str, Any]] = []
    if turn.content:
        out.append({"type": "text", "text": turn.content})
    for tc in turn.tool_calls:
        out.append({"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.arguments})
    return out


class ModelCall:
    """One model attempt: its request is written; :meth:`end` writes its response, once."""

    def __init__(self, recording: Recording, request_id: str, call: str, nested: bool) -> None:
        self._rec, self.request_id, self.call, self._nested = recording, request_id, call, nested
        self._done = False

    def end(
        self,
        status: str,
        *,
        output: list[dict[str, Any]] | None = None,
        finish_reason: str | None = None,
        error: BaseException | None = None,
        usage: Sequence[dict[str, int]] = (),
    ) -> None:
        if self._done:
            return
        self._done = True
        data: dict[str, Any] = {"status": status, "output": output or []}
        data["finish_reason"] = finish_reason or None  # null when the model gave none
        if error is not None:
            data["error"] = {
                "type": type(error).__name__,
                "message": redact(str(error))[:2000],
            }
        if usage:
            data["usage"] = {
                k: sum(u.get(k, 0) for u in usage) for k in ("prompt_tokens", "completion_tokens")
            }
        rec = self._rec
        ids = rec._write(
            [
                Entry(
                    records.MODEL_RESPONSE,
                    data,
                    turn=rec.turn,
                    call=self.call,
                    parent=self.request_id or None,
                    run=rec.run,
                )
            ]
        )
        if ids and not self._nested:
            rec.response = ids[0]


class Recording:
    """Writes the model and tool lines of one turn (or, without a turn, of one operation).

    ``write`` appends entries to the session's log and returns their ids; ``known`` is the map
    from the digest of a ``model.input`` item to the id of the line that holds it."""

    def __init__(
        self,
        write: Callable[[list[Entry]], list[str]],
        known: Callable[[], dict[str, str]],
        turn: str | None = None,
    ) -> None:
        self._append, self._known = write, known
        self.turn = turn
        self.run: str | None = None
        self.cause: str | None = None  # the line the next request follows from
        self.response: str | None = None  # the latest research response: the tool calls' parent

    def _write(self, entries: list[Entry]) -> list[str]:
        try:
            return self._append(entries)
        except Exception:  # noqa: BLE001 (the record must not stop the run it records)
            _LOG.warning("the session record could not be written", exc_info=True)
            return []

    # ----- model calls --------------------------------------------------------- #

    def model_call(
        self,
        profile: Any,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> ModelCall:
        """Write the request of one attempt (and the items it is the first to use)."""
        tool = current_tool_call()
        call = f"m{uuid.uuid4().hex[:10]}"
        try:
            request = self._request(profile, messages, tools, settings or {}, tool, call)
        except Exception:  # noqa: BLE001 (see _write)
            _LOG.warning("a model request could not be recorded", exc_info=True)
            request = ""
        return ModelCall(self, request, call, nested=tool is not None)

    def _request(
        self,
        profile: Any,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None,
        settings: dict[str, Any],
        tool: tuple[str, str] | None,
        call: str,
    ) -> str:
        known = self._known()
        fresh: dict[str, Entry] = {}
        refs: list[tuple[str, str, str]] = []  # (kind, digest, location) in request order

        def item(kind: str, value: Any, where: str) -> None:
            digest = _digest(kind, value)
            refs.append((kind, digest, where))
            if digest not in known and digest not in fresh:
                data = {"hash": digest, "kind": kind, "value": value}
                fresh[digest] = Entry(records.MODEL_INPUT, data, self.turn, run=self.run)

        rest = list(messages)
        if rest and rest[0].get("role") == "system":
            item("instructions", rest.pop(0).get("content"), "instructions")
        for n, m in enumerate(rest):
            item("item", {k: v for k, v in m.items() if k != "_pointer"}, f"input[{n}]")
        if tools:
            item("tools", list(tools), "tools")
        if fresh:
            for digest, line_id in zip(fresh, self._write(list(fresh.values())), strict=False):
                known[digest] = line_id

        data: dict[str, Any] = {
            "provider": profile.provider,
            "profile": profile.name,
            "model": profile.model,
            "settings": settings,
        }
        inputs: list[dict[str, str]] = []
        missing: list[str] = []
        for kind, digest, where in refs:
            if digest not in known:  # its definition could not be written
                missing.append(where)
                continue
            ref = {"ref": known[digest]}
            if kind == "instructions":
                data["instructions"] = ref
            elif kind == "tools":
                data["tools"] = ref
            else:
                inputs.append(ref)
        data["input"] = inputs
        if missing:
            data["complete"] = False
            data["missing"] = missing
        data = {k: v for k, v in data.items() if v is not None}
        if not tools:
            data["tools"] = None  # null: the request carried none (absent: capture failed)
        parent = tool[1] if tool else self.cause
        ids = self._write([Entry(records.MODEL_REQUEST, data, self.turn, call, parent, self.run)])
        return ids[0] if ids else ""

    # ----- tool calls ----------------------------------------------------------- #

    def tool_call(self, name: str, call_id: str, arguments: dict[str, Any]) -> str:
        """Write a ``tool.call`` line; its id (empty if it could not be written)."""
        ids = self._write(
            [
                Entry(
                    records.TOOL_CALL,
                    {"name": name, "arguments": arguments},
                    self.turn,
                    call_id,
                    self.response,
                    self.run,
                )
            ]
        )
        return ids[0] if ids else ""

    def tool_result(
        self,
        call_id: str,
        parent: str,
        status: str,
        *,
        result: Any = None,
        artifact: str | None = None,
        error: Any = None,
    ) -> None:
        """Write the ``tool.result`` of a call: inline ``result``, or the id of an artifact."""
        entries: list[Entry] = []
        data: dict[str, Any] = {"status": status}
        if artifact is not None:
            data["artifact"] = artifact
        elif result is not None:
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            if len(text) > INLINE_RESULT:
                data["artifact"] = f"result-{uuid.uuid4().hex[:10]}"
                entries += records.artifact_entries(
                    data["artifact"],
                    text,
                    media_type="text/plain",
                    kind="result",
                    turn=self.turn,
                    call=call_id,
                    parent=parent or None,
                    run=self.run,
                )
            else:
                data["result"] = result
        if error is not None:
            data["error"] = error if isinstance(error, dict) else {"message": error}
        entries.append(
            Entry(records.TOOL_RESULT, data, self.turn, call_id, parent or None, self.run)
        )
        ids = self._write(entries)
        if ids:
            self.cause = ids[-1]

    def artifact(self, artifact_id: str, content: str, *, kind: str) -> None:
        """Write ``content`` as an artifact, linked to the tool call being dispatched."""
        tool = current_tool_call()
        self._write(
            records.artifact_entries(
                artifact_id,
                content,
                kind=kind,
                turn=self.turn,
                call=tool[0] if tool else None,
                parent=tool[1] if tool else None,
                run=self.run,
            )
        )
