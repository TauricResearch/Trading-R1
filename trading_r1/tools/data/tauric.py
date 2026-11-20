# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The Tauric data API client, used by every data tool.

Base URL and key come from the `tauric` provider profile (so a machine's override of the
backbone's endpoint applies to its data too; the profile's base ends in `/v1`). A request opens
no global state: `Tauric` is an async context manager scoping one connection pool, shared by the
pages of one tool call. Transient failures (timeouts, 429, 5xx) retry under the runtime's
policy; every other failure becomes a `ToolError` carrying Tauric's own message, which already
says what to use instead ("use this key", "the standard pair is BTC-USDT"), and its `code` and
`retry`. A failure with no answer from Tauric can pass with time: its `retry` is `later`. Limits
Tauric states on the data it returns are kept in `limits` for the tool's notes (`stated_limits`),
with Tauric's reason for each null value the tool read (`unavailable`). A successful answer
whose `Cache-Control` states a `max-age` is reused until it runs out, with no request (`reuse.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from trading_r1 import USER_AGENT
from trading_r1.contract.spec import ToolError
from trading_r1.runtime.retry import RETRYABLE_STATUS, is_transient_http, with_retry
from trading_r1.state.runtime_config import runtime_config
from trading_r1.tools.data.reuse import REUSE, key_for, lifetime


def _failure(resp: Any, wrap: str = "{}", retry: str | None = None) -> ToolError:
    """Tauric's error `{"error": {"code", "message", "retry"}}`, else the status line, as a
    `ToolError`; `wrap` frames the text and `retry` stands in where Tauric states none."""
    try:
        err = resp.json()["error"]
        text, code = f"{err['message']} [{err['code']}]", err["code"]
        retry = err.get("retry") or retry
    except (ValueError, KeyError, TypeError):
        text, code = f"HTTP {resp.status_code}", None
    return ToolError(wrap.format(text), code=code, retry=retry)


# Value kinds that are not a measurement of what happened; `derived` and `unknown` say nothing
# about a value that its series' name does not.
_QUALIFIED_KINDS = frozenset({"estimate", "forecast", "projection", "model_output"})
# Where an answer says whether it is whole: `complete` (or `period_complete`), with its reason.
_COMPLETENESS = ("complete", "period_complete")
_SHOWN = 6  # missing periods named in a note, the rest counted


def _subject(node: dict[str, Any]) -> str | None:
    """The instrument or series a node of an answer is about, without its kind."""
    for field in ("instrument", "series"):
        about = dict.get(node, field)  # not a read of the answer (`Answer`)
        key = dict.get(about, "key") if isinstance(about, dict) else None
        if key:
            return key.partition(":")[2] or key
    return None


def _period(m: dict[str, Any]) -> str:
    """A period of `coverage.missing` by its calendar unit: 2025-10, 2025-Q4, 2025, a day."""
    start, kind = m["start"], m.get("kind")
    if kind == "month":
        return start[:7]
    if kind == "quarter":
        return f"{start[:4]}-Q{(int(start[5:7]) - 1) // 3 + 1}"
    if kind == "calendar_year":
        return start[:4]
    end = m.get("end") or start
    return start if end == start else f"{start} to {end}"


def _node_limits(node: dict[str, Any], who: str, dated: bool) -> Iterator[str]:
    """What one node states about itself: overdue data, an incomplete answer, periods not
    published, a last close."""
    if not dated and node.get("stale") is True:
        yield f"{who}: newer data is overdue."
    missing = node.get("missing")
    periods = (
        [m for m in missing if isinstance(m, dict) and m.get("start")]
        if isinstance(missing, list)
        else []
    )
    for field in _COMPLETENESS:
        if f"{field}_reason" not in node or node.get(field) is True:
            continue
        reason = node[f"{field}_reason"]
        if reason:
            yield f"{who}: incomplete ({reason})."
        elif node.get(field) is False and not periods:  # missing periods say what is short
            yield f"{who}: incomplete."
    by_reason: dict[str | None, list[str]] = {}
    for m in periods:
        by_reason.setdefault(m.get("reason"), []).append(_period(m))
    for reason, labels in by_reason.items():
        shown = ", ".join(labels[:_SHOWN]) + (
            f" and {len(labels) - _SHOWN} more" if len(labels) > _SHOWN else ""
        )
        state = f"not served whole ({reason})" if reason else "not published"
        yield f"{who}: {state}: {shown}."
    if node.get("quote_kind") == "last_close":
        yield f"{who}: the price is the {node.get('session_date')} close, not a live quote."


def stated_limits(body: Any, *, dated: bool = False) -> list[str]:
    """What Tauric states limits the values in an answer, as notes for its reader: data overdue,
    an answer that is not complete (with Tauric's reason), a last close standing for a quote, a
    value marked provisional, a value that is not an observation. Nothing when the answer is
    clean. An answer read up to a given date (`dated`) says nothing about how current the data
    is: Tauric marks any window that ends in the past as overdue."""
    notes: list[str] = []
    provisional: dict[str, list[str]] = {}
    kinds: dict[tuple[str, str], list[str]] = {}
    whole: set[tuple[str, str]] = set()  # a series that is such throughout, not period by period

    def scan(node: Any, who: str, path: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for item in node:
                scan(item, who, path)
        if not isinstance(node, dict):
            return
        if subject := _subject(node):
            who, path = subject, ()
        notes.extend(n for n in _node_limits(node, who, dated) if n not in notes)
        states = node.get("field_states")
        for field, state in states.items() if isinstance(states, dict) else ():
            if isinstance(state, dict) and state.get("reason") == "provisional":
                at = [p for p in path if p not in ("data", "values")]
                period = node.get("period")
                end = period.get("end") if isinstance(period, dict) else None
                when = f" ({end})" if end else ""
                provisional.setdefault(who, []).append(".".join((*at, field)) + when)
        if (kind := node.get("value_kind")) in _QUALIFIED_KINDS:
            period = node.get("period")
            labels = kinds.setdefault((who, kind), [])
            if isinstance(period, dict):
                labels.append(period.get("label"))
            else:
                whole.add((who, kind))
        for key, value in node.items():
            if key != "field_states":
                scan(value, who, (*path, key))

    scan(body, "This data", ())
    for who, fields in provisional.items():
        names = ", ".join(dict.fromkeys(fields))
        notes.append(f"{who}: marked provisional, check before relying on it: {names}.")
    for (who, kind), labels in kinds.items():
        which = "the whole series" if (who, kind) in whole else ", ".join(dict.fromkeys(labels))
        notes.append(f"{who}: not observed, {kind.replace('_', ' ')}: {which}.")
    return notes


NOT_PUBLISHED = ("not_found", "not_supported", "not_published")  # a settled absence


class Answer(dict):
    """A JSON object of an answer that remembers which of its fields a tool read (`get`, `[]`),
    so that a value the tool did not use is not explained to its reader."""

    __slots__ = ("read",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read: set[str] = set()

    def get(self, key: Any, default: Any = None) -> Any:
        self.read.add(key)
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        self.read.add(key)
        return super().__getitem__(key)


def answer(node: Any) -> Any:
    """`node` with every JSON object an `Answer`."""
    if isinstance(node, dict):
        return Answer({k: answer(v) for k, v in node.items()})
    if isinstance(node, list):
        return [answer(v) for v in node]
    return node


def unavailable(body: Any) -> list[str]:
    """For each value a tool read that is null with a `field_states` entry, Tauric's reason,
    and "ask again later" when its `retry` is `later`. A plain null says nothing; a provisional
    value is noted by `stated_limits`."""
    grouped: dict[tuple[str, str, str | None], list[str]] = {}

    def scan(node: Any, who: str, path: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for item in node:
                scan(item, who, path)
        if not isinstance(node, Answer):
            return
        if subject := _subject(node):
            who, path = subject, ()
        states = dict.get(node, "field_states")
        for field, state in states.items() if isinstance(states, dict) else ():
            if (
                field in node.read
                and dict.get(node, field, 0) is None
                and isinstance(state, dict)
                and state.get("reason") not in (None, "provisional")
            ):
                at = [p for p in path if p not in ("data", "values")]
                key = (who, state["reason"], state.get("retry"))
                grouped.setdefault(key, []).append(".".join((*at, field)))
        for key, value in node.items():
            if key != "field_states":
                scan(value, who, (*path, key))

    scan(body, "This data", ())
    notes = []
    for (who, reason, retry), fields in grouped.items():
        later = "; ask again later" if retry == "later" else ""
        notes.append(f"{who}: {', '.join(dict.fromkeys(fields))} not available ({reason}){later}.")
    return notes


class Tauric:
    """`async with Tauric() as api: body = await api.get("/securities/AAPL/quote")`."""

    def __init__(self) -> None:
        from trading_r1.providers.profiles import get_profile

        profile = get_profile("tauric")
        self._base = profile.base_url.rstrip("/")
        self._key = profile.resolve_api_key()
        self._client: Any = None
        self._stated: list[str] = []  # what the answers so far state about themselves
        self._answers: list[Any] = []

    async def __aenter__(self) -> Tauric:
        import httpx

        if not self._key:
            raise ToolError(
                "TAURIC_API_KEY is not set. Market data needs it: register with Tauric, then "
                "`export TAURIC_API_KEY=…` or run "
                "`trading-r1 secrets set TAURIC_API_KEY`"
            )
        data = runtime_config().data
        timeout = httpx.Timeout(data.read_timeout_s, connect=data.connect_timeout_s)
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"Authorization": f"Bearer {self._key}", "User-Agent": USER_AGENT},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        """GET `path` (under `/v1`) with the non-None params; the JSON body."""
        query = {k: v for k, v in params.items() if v is not None}
        key = key_for(self._base, self._key, path, query)
        if (kept := REUSE.get(key)) is not None:
            return self._keep_limits(kept, dated="end" in query or "date" in query)
        resp, body = await self._send("GET", path, params=query)
        if resp.status_code == 200:
            REUSE.put(
                key, body, lifetime(resp.headers.get("cache-control"), resp.headers.get("age"))
            )
        return self._keep_limits(body, dated="end" in query or "date" in query)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST a read-only batch request (`/quotes`); the JSON body. Never reused."""
        return self._keep_limits((await self._send("POST", path, json=body))[1])

    def _keep_limits(self, body: Any, dated: bool = False) -> Any:
        found = stated_limits(body, dated=dated)
        self._stated.extend(n for n in found if n not in self._stated)
        self._answers.append(kept := answer(body))
        return kept

    @property
    def limits(self) -> list[str]:
        """What the answers so far state, for the tool's notes: `stated_limits`, and why a value
        the tool read is null (`unavailable`)."""
        notes = list(self._stated)
        for body in self._answers:
            notes.extend(n for n in unavailable(body) if n not in notes)
        return notes

    async def _send(self, method: str, path: str, **kwargs: Any) -> tuple[Any, Any]:
        import httpx

        async def once() -> Any:
            resp = await self._client.request(method, path, **kwargs)
            if resp.status_code in RETRYABLE_STATUS:
                resp.raise_for_status()
            return resp

        try:
            resp = await with_retry(
                once, max_attempts=runtime_config().data.attempts, retryable=is_transient_http
            )
        except httpx.HTTPStatusError as err:
            raise _failure(
                err.response, "Tauric data API unavailable ({}): try again shortly", "later"
            ) from err
        except httpx.TransportError as err:
            raise ToolError(
                f"Tauric data API unreachable ({type(err).__name__})", retry="later"
            ) from err
        if resp.status_code in (401, 403):
            raise _failure(resp, "Tauric rejected the API key ({})")
        if resp.status_code >= 400:
            raise _failure(resp)
        try:
            return resp, resp.json()
        except ValueError as err:
            raise ToolError("Tauric answered with a body that is not JSON") from err


def provenance(
    *,
    subject: str | None = None,
    view: str | None = None,
    as_of: str | None = None,
    source: str | None = None,
) -> dict[str, str]:
    """A data tool's meta: the publisher of the data (`source`; Tauric where it publishes the
    data itself); what the call is about (`subject`, `view`) as the tool resolved it; and where
    known the date the data runs to (`as_of`, ISO). Kept in the evidence record and shown in
    the rail."""
    out = {"source": source or "Tauric"}
    if as_of:
        out["as_of"] = as_of[:10]
    if subject:
        out["subject"] = subject
    if view:
        out["view"] = view
    return out
