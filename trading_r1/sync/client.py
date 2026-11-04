# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The archive's HTTP calls, with errors typed from the response's ``error.code``.

Offsets, sizes and ranges all count decoded bytes. A body above 64 KiB is sent gzip-compressed.
The API key is sent as a bearer header only; it appears in no message raised from here.
"""

from __future__ import annotations

import functools
import gzip
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.tauric.ai/v1"
REQUEST_S = 30.0
APPLICATION = "tauric"
MAX_APPEND = 4 * 1024 * 1024  # decoded bytes per append
GZIP_ABOVE = 64 * 1024
LIST_LIMIT = 100

_NOT_AUTHORISED = {
    "missing_api_key",
    "invalid_api_key",
    "api_key_inactive",
    "api_key_expired",
    "insufficient_scope",
    "principal_not_permitted",
    "forbidden",
}


class ArchiveError(Exception):
    """A call the archive refused or that could not be completed."""

    def __init__(self, message: str, *, code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.code, self.status = code, status


class NotAuthorisedError(ArchiveError):
    """The key is missing, wrong or expired, or the account may not use the application."""


class UnreadableAnswerError(ArchiveError):
    """The archive answered in a shape this version does not read; try again later."""


class _SizedError(ArchiveError):
    """A refused append that reports the archive's durable ``size``."""

    def __init__(self, message: str, *, size: int, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.size = size


class OffsetMismatchError(_SizedError):
    """The append starts past the archive's durable size."""


class DivergedError(_SizedError):
    """The archive holds different bytes at the offset: another writer continued the session."""


class ArchiveFullError(ArchiveError):
    """The account's allowance cannot hold the append; ``remaining`` bytes would still fit."""

    def __init__(self, message: str, *, remaining: int, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.remaining = remaining


class DeletedError(ArchiveError):
    """The session was deleted; its id stays reserved."""


class TooLargeError(ArchiveError):
    """The append is over the size bound: send fewer lines."""


class RateLimitedError(ArchiveError):
    """Too many requests; ``retry_after`` is the server's hint in seconds, when it gave one."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class NotFoundError(ArchiveError):
    """No such application or session for this account."""


class RejectedError(ArchiveError):
    """The request is not acceptable and will not become so by repeating it."""


class TransientError(ArchiveError):
    """The network or the server failed; try again later."""


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _error_for(response: httpx.Response, scrub: Callable[[str], str]) -> ArchiveError:
    status = response.status_code
    try:
        body = response.json()["error"]
        code, message = str(body.get("code", "")), scrub(str(body.get("message", "")))
    except (ValueError, KeyError, TypeError, AttributeError):
        body, code, message = {}, "", ""
    text = f"{status} {code or 'error'}: {message}".rstrip(": ")
    kw: dict[str, Any] = {"code": code, "status": status}
    if code in _NOT_AUTHORISED or (status == 401 and not code):
        return NotAuthorisedError(text, **kw)
    if code == "session_diverged":
        return DivergedError(text, size=int(body.get("size", 0)), **kw)
    if code == "session_offset_mismatch":
        return OffsetMismatchError(text, size=int(body.get("size", 0)), **kw)
    if code == "archive_full":
        return ArchiveFullError(text, remaining=int(body.get("remaining_bytes", 0)), **kw)
    if code == "session_deleted":
        return DeletedError(text, **kw)
    if code == "request_too_large":
        return TooLargeError(text, **kw)
    if code == "rate_limit_exceeded" or status == 429:
        return RateLimitedError(text, retry_after=_retry_after(response), **kw)
    if code == "not_found":
        return NotFoundError(text, **kw)
    if status >= 500 or code in ("server_error", "service_unavailable"):
        return TransientError(text, **kw)
    return RejectedError(text, **kw)


def _view(view: dict[str, Any]) -> dict[str, Any]:
    """A session as the archive describes it, in the shape everything here relies on."""
    metadata = view.get("metadata")
    return {
        **view,
        "size": int(view["size"]),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _read(call: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """An answer in a shape this client does not read fails as the archive being out of reach:
    the session keeps its place, says why, and is tried again."""

    @functools.wraps(call)
    async def reading(*args: Any, **kw: Any) -> Any:
        try:
            return await call(*args, **kw)
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            # a missing field's name, never a value: an answer can repeat what it was sent
            what = f"no {err}" if isinstance(err, KeyError) else type(err).__name__
            raise UnreadableAnswerError(
                f"your account answered in a form this version does not read ({what})"
            ) from None

    return reading


class ArchiveClient:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        application: str = APPLICATION,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = REQUEST_S,
    ) -> None:
        self._key = key
        self.application = application
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        await self._http.aclose()

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kw)
        except httpx.HTTPError as err:
            raise TransientError(self._scrub(f"{type(err).__name__} on {method} {path}")) from None
        if response.status_code >= 400:
            raise _error_for(response, self._scrub)
        return response

    def _session(self, session_id: str, tail: str = "") -> str:
        return f"applications/{self.application}/sessions/{session_id}{tail}"

    # -- calls ----------------------------------------------------------------------------
    @_read
    async def usage(self) -> dict[str, int]:
        """``{used_bytes, allowance_bytes}`` of the account's archive."""
        storage = (await self._call("GET", "account/usage")).json()["account"]["storage"]
        return {
            "used_bytes": storage["archive_bytes"],
            "allowance_bytes": storage["archive_allowance_bytes"],
        }

    @_read
    async def list_sessions(
        self, *, limit: int = LIST_LIMIT, after: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One page of the sessions, newest first, and the cursor of the next page."""
        params: dict[str, Any] = {"limit": limit} | ({"after": after} if after else {})
        path = f"applications/{self.application}/sessions"
        body = (await self._call("GET", path, params=params)).json()
        return [_view(v) | {"session_id": str(v["session_id"])} for v in body["data"]], body.get(
            "next_cursor"
        )

    @_read
    async def put_session(
        self,
        session_id: str,
        label: str | None = None,
        parent: dict[str, Any] | None = None,
        details: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create the session, or update its metadata, which is replaced whole: its label and
        ``details`` for listing and filtering. Returns its metadata."""
        metadata = {k: v[:512] for k, v in (details or {}).items() if v}
        if label:
            metadata["label"] = label[:200]
        body = {"metadata": metadata, "parent": parent}
        return _view((await self._call("PUT", self._session(session_id), json=body)).json())

    @_read
    async def metadata(self, session_id: str) -> dict[str, Any]:
        return _view((await self._call("GET", self._session(session_id))).json())

    async def read(self, session_id: str, start: int, end: int | None = None) -> bytes:
        """The stored bytes from ``start`` to ``end`` (inclusive; to the end when omitted)."""
        spec = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
        path = self._session(session_id, "/content")
        return (await self._call("GET", path, headers={"Range": spec})).content

    @_read
    async def append(self, session_id: str, offset: int, body: bytes) -> tuple[int, int]:
        """Append whole lines at ``offset``. Returns ``(size, verified_to)``."""
        headers = {"Upload-Offset": str(offset), "Content-Type": "application/jsonl"}
        content = body
        if len(body) > GZIP_ABOVE:
            packed = gzip.compress(body, mtime=0)
            if len(packed) < len(body):
                content, headers["Content-Encoding"] = packed, "gzip"
        path = self._session(session_id, "/content")
        result = (await self._call("PATCH", path, content=content, headers=headers)).json()
        return int(result["size"]), int(result["verified_to"])

    async def delete(self, session_id: str) -> None:
        """Delete the session in the archive; repeatable, also for an id never uploaded."""
        await self._call("DELETE", self._session(session_id))
