# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Fetch/parsing helpers for the `visit` tool.

This module owns network retrieval and native HTML/PDF text extraction. The
tool-facing module keeps orchestration and rendering concerns separate.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from trading_r1.state.runtime_config import runtime_config


@dataclass
class FetchedPage:
    """Raw fetched page text or a fetch failure reason."""

    url: str
    text: str
    ok: bool
    error: str | None = None


Fetcher = Callable[[str], Awaitable[FetchedPage]]

_MAX_BODY_BYTES = 10 * 1024 * 1024  # a larger page is refused rather than held in memory

# Native HTML/PDF parsers (lxml via trafilatura, pdfplumber) are CPU-bound C
# extensions that corrupt the heap when run in parallel threads. Serialize every
# parse through one dedicated worker while network fetches stay concurrent.
_PARSE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tr1-parse")
_PARSE_TIMEOUT_S = 25.0
_PARSE_QUEUE_S = 120.0  # the longest a parse waits for the worker
# A parse that runs past its limit cannot be interrupted: the caller stops waiting but the worker
# stays on it until it returns. Pages are capped at _MAX_BODY_BYTES before they reach it, which
# bounds the input such a parse can be given.

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_FETCH_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_RETRY_STATUS = frozenset({403, 408, 425, 429, 500, 502, 503, 504})


async def _parse(fn: Callable[..., str], *args: object) -> str:
    """Run a native parser on the single parse worker. The parse's time limit starts when it
    starts, not while it waits behind other runs' parses (concurrent runs share the worker);
    the wait itself is bounded separately."""
    loop = asyncio.get_running_loop()
    started = asyncio.Event()

    def run() -> str:
        loop.call_soon_threadsafe(started.set)
        return fn(*args)

    job = loop.run_in_executor(_PARSE_POOL, run)
    try:
        await asyncio.wait_for(started.wait(), timeout=_PARSE_QUEUE_S)
    except TimeoutError:
        job.cancel()  # still queued: it never runs
        raise
    return await asyncio.wait_for(job, timeout=_PARSE_TIMEOUT_S)


def _extract_pdf(content: bytes) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


class BlockedAddressError(Exception):
    """The page is on this machine or a private network: not a public page to read."""


def _non_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global


async def _public_only(request: httpx.Request) -> None:
    """Request hook, run for the first request and for every redirect: refuse a host that is, or
    resolves to, a loopback, private, link-local or otherwise non-public address. (A name that
    changes its answer between this check and the request is not caught.)"""
    host = request.url.host
    try:
        addresses = [host] if _is_ip(host) else await _resolve(host)
    except OSError:
        return  # unresolvable: the request fails by itself
    if any(_non_public(a) for a in addresses):
        raise BlockedAddressError(host)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return True


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def _fetch_once(url: str, timeout: float) -> FetchedPage:
    """One GET -> extracted text; retryable failures carry a ``retryable:`` prefix."""
    import trafilatura

    try:
        async with asyncio.timeout(timeout):
            async with (
                httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=timeout,
                    headers=_FETCH_HEADERS,
                    event_hooks={"request": [_public_only]},
                ) as client,
                client.stream("GET", url) as resp,
            ):
                if resp.status_code in _RETRY_STATUS:
                    return FetchedPage(
                        url=url, text="", ok=False, error=f"retryable:HTTP {resp.status_code}"
                    )
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_BODY_BYTES:
                        return FetchedPage(url=url, text="", ok=False, error="page too large")
                ctype = resp.headers.get("content-type", "").lower()
                encoding = resp.encoding or "utf-8"
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            text = await _parse(_extract_pdf, bytes(body))
        else:
            text = await _parse(trafilatura.extract, body.decode(encoding, errors="replace"))
        text = (text or "").strip()
        if not text:
            return FetchedPage(url=url, text="", ok=False, error="retryable:no extractable content")
        return FetchedPage(url=url, text=text, ok=True)
    except BlockedAddressError:
        return FetchedPage(url=url, text="", ok=False, error="not a public address")
    except Exception as exc:  # noqa: BLE001  # surfaced as a failed page, never raised
        return FetchedPage(
            url=url, text="", ok=False, error=f"retryable:{type(exc).__name__}: {exc}"
        )


def _settled(page: FetchedPage) -> FetchedPage:
    """The page as the caller sees it: a failure no longer carries the ``retryable:`` marker."""
    if not page.ok and (page.error or "").startswith("retryable:"):
        return FetchedPage(url=page.url, text="", ok=False, error=page.error[len("retryable:") :])
    return page


async def quick_fetch(url: str, *, timeout: float | None = None) -> FetchedPage:
    """Single-attempt fetch for quick search-result readability validation."""
    return _settled(await _fetch_once(url, timeout or runtime_config().fetch.quick_timeout_s))


async def http_fetch(url: str, *, timeout: float | None = None) -> FetchedPage:
    """Default fetcher: HTTP GET -> extracted text, retrying transient failures."""
    page = FetchedPage(url=url, text="", ok=False, error="no attempt")
    fetch = runtime_config().fetch
    for attempt in range(fetch.attempts):
        page = await _fetch_once(url, timeout or fetch.timeout_s)
        if page.ok or not (page.error or "").startswith("retryable:"):
            break
        if attempt < fetch.attempts - 1:
            await asyncio.sleep(fetch.backoff_s * (attempt + 1))
    return _settled(page)
