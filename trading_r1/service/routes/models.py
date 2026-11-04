# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Backbone routes: ``GET /health`` (readiness of one backbone and its paired reader),
``GET /models`` (every selectable backbone with live readiness, the data of the ``/model``
picker) and ``GET /capabilities`` (the tools and skills catalog). A probe that fails means
not-ready, never an error response.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from trading_r1.assembly import default_skill_registry, model_facing_registry
from trading_r1.providers import (
    ModelProfile,
    ProfileSelectionError,
    available_backbones,
    default_backbone_choice,
    get_profile,
    load_profiles,
)
from trading_r1.providers.health import pair_status
from trading_r1.runtime.capabilities import capabilities_catalog
from trading_r1.service import state
from trading_r1.state.runtime_config import runtime_config

router = APIRouter()


async def _readiness(
    row: dict[str, Any], all_profiles: dict[str, ModelProfile], client: httpx.AsyncClient
) -> dict[str, Any]:
    """Effective readiness for one backbone row: usable only if BOTH the backbone and its
    paired reader answer and serve their model. An unconfigured cloud backbone is not probed.
    Returns a structured ``reason`` (``ok`` / ``needs_key:<ENV>`` / ``key_refused`` /
    ``offline:backbone`` / ``offline:reader``) so the picker can give the right guidance."""
    if not row["configured"]:
        return {"ready": False, "reason": f"needs_key:{row['api_key_env']}"}
    backbone = all_profiles[row["name"]]
    reader = all_profiles.get(backbone.reader_profile) if backbone.reader_profile else None
    if reader is None:  # a misconfigured pairing is reported without a probe
        return {"ready": False, "reason": "offline:reader"}
    b_status, r_status = await pair_status(backbone, reader, client)
    ready = b_status == "ok" and r_status == "ok"
    if ready:
        reason = "ok"
    elif "key_refused" in (b_status, r_status):
        reason = "key_refused"
    else:
        reason = "offline:backbone" if b_status != "ok" else "offline:reader"
    return {"ready": ready, "reason": reason}


@router.get("/health")
async def health(profile: str | None = None) -> dict[str, Any]:
    """Readiness of a backbone + its paired reader. ``profile`` selects which backbone to
    report; with none given it reports the auto-default (a typo'd explicit selection is a 400)."""
    backbone = state.resolve_profile(profile)
    try:  # a backbone whose declared reader can't resolve is degraded (never backbone-as-reader)
        reader = get_profile(backbone.reader_profile) if backbone.reader_profile else None
    except ProfileSelectionError:
        reader = None
    async with httpx.AsyncClient(timeout=runtime_config().probe.timeout_s) as client:
        b_status, r_status = await pair_status(backbone, reader, client)
        b_ready, r_ready = b_status == "ok", r_status == "ok"
    return {
        "status": "ok" if (b_ready and r_ready) else "degraded",
        "version": state.API_VERSION,
        "backbone": {"profile": backbone.name, "model": backbone.model, "ready": b_ready},
        "reader": {
            "profile": reader.name if reader else None,
            "model": reader.model if reader else None,
            "ready": r_ready,
        },
    }


@router.get("/models")
async def models() -> dict[str, Any]:
    """The selectable backbones in stable order with live readiness, for the `/model`
    picker. Each row carries ``configured`` (credentials present), effective ``ready``
    (backbone AND paired reader answer), and a structured ``reason``. ``default`` is the
    auto-default (explicit selection, else the first set up, else none); the TUI prefers a
    *ready* one over it. Readiness probes run concurrently over a shared client; a probe that
    fails is not-ready, and the others still report."""
    try:
        rows = available_backbones()
        all_profiles = load_profiles()
    except ProfileSelectionError as e:  # an unusable providers file: say so, do not list defaults
        raise HTTPException(status_code=400, detail=str(e)) from e

    async def _row_readiness(row: dict[str, Any]) -> dict[str, Any]:
        try:
            return await _readiness(row, all_profiles, client)
        except Exception:  # noqa: BLE001 (a probe failure is not-ready, never a 500)
            return {"ready": False, "reason": "offline:backbone"}

    async with httpx.AsyncClient(timeout=runtime_config().probe.timeout_s) as client:
        readiness = await asyncio.gather(*(_row_readiness(r) for r in rows))
    # ``order`` is an internal sort key; the array is already ordered, so it's not sent.
    models_out = [
        {**{k: v for k, v in row.items() if k != "order"}, **rd}
        for row, rd in zip(rows, readiness, strict=True)
    ]
    try:
        default, reason = default_backbone_choice()
    except ProfileSelectionError:
        default, reason = None, None  # a typo'd TR1_PROFILE keeps the inventory usable
    return {"models": models_out, "default": default, "default_reason": reason}


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    """The browsable tools + skills catalog, grouped by domain, for the TUI's
    "browse tools & skills" view. Mode-independent (every capability is listed); the
    same taxonomy the prompt catalog uses, so the two never drift."""
    skills = default_skill_registry()
    return capabilities_catalog(model_facing_registry(skills), skills)
