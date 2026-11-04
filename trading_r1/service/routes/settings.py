# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Settings routes: the persisted user settings (``GET``/``PUT /settings``: the sticky backbone
and the preferences, each reported as the value in effect) and the agent's remembered notes
(``GET /memory``, ``DELETE /memory/{name}``). A preference sent as null is forgotten; a value the
setting does not accept is a 400.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ConfigDict, Field, create_model

from trading_r1.providers import available_backbones
from trading_r1.service import preferences, state

_LOG = logging.getLogger(__name__)
router = APIRouter()


# A settings change: the backbone and any preference, each sent as null to be forgotten (the
# default applies again). What a preference accepts is checked against ``preferences.PREFERENCES``.
SettingsUpdate = create_model(
    "SettingsUpdate",
    __config__=ConfigDict(extra="forbid"),
    backbone=(str | None, Field(None, description="The backbone to persist as the default.")),
    **{key: (Any, None) for key in preferences.PREFERENCES},
)


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    """The persisted user settings. ``backbone`` is present only when the user chose one with
    ``/model``; the TUI prefers it over a merely ready one, else the picker auto-selects.
    The preferences are the values in effect (the user's choice, else the default); ``problem``
    says why the file could not be used, when it could not."""
    own, problem = preferences.SETTINGS.read()
    return {**own, **preferences.current(), **({"problem": problem} if problem else {})}


@router.put("/settings")
async def put_settings(req: SettingsUpdate) -> dict[str, Any]:  # type: ignore[valid-type]
    """Persist a settings change: the backbone (called by ``/model``) and/or preferences. Every
    part is checked first and the file written once, so a request with a part that is refused
    changes nothing (400)."""
    changes = {key: getattr(req, key) for key in req.model_fields_set}
    backbone = changes.pop("backbone", None)
    if backbone is not None and backbone not in {r["name"] for r in available_backbones()}:
        raise HTTPException(status_code=400, detail=f"unknown backbone {backbone!r}")
    try:
        checked = preferences.check(changes)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    preferences.SETTINGS.update(
        {**checked, **({"backbone": backbone} if backbone is not None else {})}
    )
    if "sync" in checked:
        try:
            state.sync().apply()  # a switch that is turned off stops sending now
        except Exception:  # noqa: BLE001 (the setting is saved; a pass reads it again)
            _LOG.exception("the sync switch was not applied")
    return await get_settings()


@router.get("/memory")
async def list_notes() -> dict[str, Any]:
    """What the agent remembers about the user: the notes it saved in chat sessions."""
    return {
        "notes": [
            {"name": m.name, "type": m.type, "description": m.description, "updated": m.updated}
            for m in state.MEMORY.all()
        ]
    }


@router.delete("/memory/{name}")
async def forget_note(name: str) -> dict[str, Any]:
    """Forget one note. ``deleted`` is false when there is none by that name."""
    return {"deleted": state.MEMORY.delete(name)}
