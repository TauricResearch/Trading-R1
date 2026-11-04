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
Tauric states on the data it returns are kept in `limits` for the tool's notes (`stated_limits`).
A successful answer
whose `Cache-Control` states a `max-age` is reused until it runs out, with no request (`reuse.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

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
_REGISTRANT_LIMITS = {
    "possibly_incomplete": "the SEC data may not include the newest records",
    "temporarily_unavailable": "no current SEC data can be served yet, this is what was held",
    "pending": "the latest 10-K or 10-Q's facts are not in yet",
}


def _subject(node: dict[str, Any]) -> str | None:
    """The instrument or series a node of an answer is about, without its kind."""
    for field in ("instrument", "series"):
        about = node.get(field)
        if isinstance(about, dict) and about.get("key"):
            return about["key"].partition(":")[2] or about["key"]
    return None


def _node_limits(node: dict[str, Any], who: str, dated: bool) -> Iterator[str]:
    """What one node states about itself: overdue data, a gap in its coverage, a stale price."""
    fresh, cov = node.get("freshness"), node.get("coverage")
    if not dated and isinstance(fresh, dict) and fresh.get("stale") is True:
        yield f"{who}: newer data is overdue."
    if isinstance(cov, dict):
        if cov.get("complete") is False and cov.get("complete_reason") == "possibly_incomplete":
            yield f"{who}: days may be missing from this history."
        if not dated and cov.get("pending_session"):
            yield (
                f"{who}: history ends {cov.get('through')}; the {cov['pending_session']} session "
                "has closed and its bar is not available yet."
            )
        if cov.get("not_yet_served"):
            yield f"{who}: {cov['not_yet_served']} filings the SEC lists are not served yet."
    if node.get("quote_kind") == "last_close":
        yield f"{who}: the price is the {node.get('session_date')} close, not a live quote."
    if node.get("cik") and (limit := _REGISTRANT_LIMITS.get(node.get("status"))):
        yield f"{who}: {limit} (registrant {node['cik']})."
    if isinstance(node.get("not_covered"), list) and node["not_covered"]:
        yield "No observations available for: " + ", ".join(node["not_covered"]) + "."
    if isinstance(node.get("status_only"), dict) and node["status_only"]:
        yield "No value published for: " + ", ".join(node["status_only"]) + "."


def stated_limits(body: Any, *, dated: bool = False) -> list[str]:
    """What Tauric states limits the values in an answer, as notes for its reader: data overdue,
    a history short of a closed session, a last close standing for a quote, a value marked
    unconfirmed, a value that is not an observation. Nothing when the answer is clean. Tauric
    states other limits field by field; these are the ones that change how a value reads. An answer
    read up to a given date (`dated`) says nothing about how current the data is: Tauric marks any
    window that ends in the past as overdue."""
    notes: list[str] = []
    unconfirmed: dict[str, list[str]] = {}
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
            if (state["state"], state["reason"]) == ("unverified", "unconfirmed"):
                at = [p for p in path if p not in ("concepts", "values")]
                when = f" ({node['period_end']})" if node.get("period_end") else ""
                unconfirmed.setdefault(who, []).append(".".join((*at, field)) + when)
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
    for who, fields in unconfirmed.items():
        names = ", ".join(dict.fromkeys(fields))
        notes.append(f"{who}: marked unconfirmed, check before relying on it: {names}.")
    for (who, kind), labels in kinds.items():
        which = "the whole series" if (who, kind) in whole else ", ".join(dict.fromkeys(labels))
        notes.append(f"{who}: not observed, {kind.replace('_', ' ')}: {which}.")
    return notes


NOT_PUBLISHED = ("not_found", "not_supported")  # a settled absence, not a failure to read


class Tauric:
    """`async with Tauric() as api: body = await api.get("/securities/AAPL/quote")`."""

    def __init__(self) -> None:
        from trading_r1.providers.profiles import get_profile

        profile = get_profile("tauric")
        self._base = profile.base_url.rstrip("/")
        self._key = profile.resolve_api_key()
        self._client: Any = None
        self.limits: list[str] = []  # what the answers so far state, for the tool's notes

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
            base_url=self._base, timeout=timeout, headers={"Authorization": f"Bearer {self._key}"}
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
        self.limits.extend(n for n in found if n not in self.limits)
        return body

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
