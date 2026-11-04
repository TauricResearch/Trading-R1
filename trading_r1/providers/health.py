# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Endpoint readiness: does a profile's server answer, and does it serve the model?

Shared by the service (the model picker's readiness column) and the start of every run: a
run on a local provider that isn't answering fails in seconds with the fix.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from trading_r1.providers import provider_for
from trading_r1.providers.profiles import ModelProfile
from trading_r1.state.runtime_config import runtime_config


class ModelServerUnreachableError(RuntimeError):
    """A local provider (or its reader) did not answer before a run started."""


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
Status = Literal["ok", "unreachable", "model_missing", "key_refused"]


def is_local(profile: ModelProfile) -> bool:
    """A self-hosted endpoint (vLLM, or a tunnel to one): a backbone the profiles mark
    local, or any profile (a reader too) on a loopback ``base_url``. Its ``/models`` is an
    authoritative inventory, and a run can check it cheaply."""
    marked = bool(profile.display and profile.display.kind == "local")
    return marked or urlparse(profile.base_url or "").hostname in _LOOPBACK


def model_served(payload: Any, want: str) -> bool:
    """Is ``want`` among the models the endpoint advertises? Matches an exact id or a
    path-suffixed one (Gemini lists ``models/gemini-3.8-flash``). Used only for local
    endpoints, whose ``/models`` is an authoritative inventory, so an empty or unparseable
    listing means the model is NOT available (e.g. a server with the model not loaded), not "trust
    the 200". Returns False in those cases so verification can't pass on a bare server."""
    try:
        ids = [str(m.get("id", "")) for m in (payload.get("data") or [])]
    except Exception:  # noqa: BLE001 (unexpected shape: the model is not confirmed, so not served)
        return False
    return any(mid == want or mid.rsplit("/", 1)[-1] == want for mid in ids)


async def endpoint_status(
    profile: ModelProfile, client: httpx.AsyncClient, *, verify_model: bool = False
) -> tuple[Status, list[str]]:
    """``(status, served model ids)`` for the profile's endpoint: whether ``/models`` answers,
    and, with ``verify_model``, whether ``profile.model`` is among what it serves. Sends the
    API key (a cloud ``/models`` 401s unauthenticated, read as unreachable); a keyless local
    endpoint (key ``EMPTY``) is probed without auth. A shared client lets a batch reuse connections.

    ``verify_model`` is for *local* endpoints, whose ``/models`` is an authoritative inventory
    of what's loaded (it catches the real local failure: server up, model not pulled or served
    under another name). A cloud ``/models`` is a broad catalog, not our allowlist, so it isn't
    gated on: a wrong model surfaces at call time instead of falsely reading offline."""
    url, headers = provider_for(profile).models_request()
    try:
        r = await client.get(url, headers=headers)
    except Exception:  # noqa: BLE001 (unreachable endpoint: not ready)
        return "unreachable", []
    if r.status_code in (401, 403):
        return "key_refused", []
    if r.status_code != 200:
        return "unreachable", []
    if not verify_model:
        return "ok", []
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 (a local 200 whose body can't be read can't confirm the model)
        return "model_missing", []
    ids = [str(m.get("id", "")) for m in (payload.get("data") or []) if isinstance(m, dict)]
    return ("ok" if model_served(payload, profile.model) else "model_missing"), ids


async def pair_status(
    backbone: ModelProfile, reader: ModelProfile | None, client: httpx.AsyncClient
) -> tuple[Status, Status]:
    """Probe a backbone and its paired reader concurrently (model-verifying local endpoints).
    A run needs both, and a backbone whose declared reader is missing is never usable: that
    reader reads ``unreachable``, since the backbone is never probed in its place."""

    async def status(profile: ModelProfile) -> Status:
        return (await endpoint_status(profile, client, verify_model=is_local(profile)))[0]

    if reader is None:
        return await status(backbone), "unreachable"
    first, second = await asyncio.gather(status(backbone), status(reader))
    return first, second


async def ensure_local_ready(backbone: ModelProfile, reader: ModelProfile) -> None:
    """Before a run: check each *local* endpoint of the pair (a cloud one is left unprobed)
    and raise :class:`ModelServerUnreachableError` saying, per endpoint, what is wrong."""
    pair = [backbone] if reader.name == backbone.name else [backbone, reader]
    local = [p for p in pair if is_local(p)]
    if not local:
        return
    probe = runtime_config().probe
    async with httpx.AsyncClient(timeout=probe.timeout_s) as client:
        for attempt in range(probe.attempts):  # a busy server can miss one probe and be fine
            if attempt:
                await asyncio.sleep(probe.pause_s)
            statuses = await asyncio.gather(
                *(endpoint_status(p, client, verify_model=True) for p in local)
            )
            if all(status != "unreachable" for status, _ in statuses):
                break
    problems = []
    for p, (status, ids) in zip(local, statuses, strict=True):
        where = p.name  # the message reaches the session log: no address
        if status == "unreachable":
            problems.append(f"{where} is not answering: is its server up, or the tunnel to it?")
        elif status == "key_refused":
            problems.append(f"{where} refused its key")
        elif status == "model_missing":
            served = ", ".join(ids) or "nothing"
            problems.append(f"{where} answers but doesn't serve {p.model!r} (it serves: {served})")
    if problems:
        raise ModelServerUnreachableError("local provider not ready: " + "; ".join(problems))
