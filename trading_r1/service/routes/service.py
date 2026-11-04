# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Background-service routes: whether the backend is installed as a user service
(``GET /service``) and installing or removing it (``POST /service/install``, ``DELETE
/service``). Changing the service is refused with a 403 unless the API token is on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from trading_r1.service.auth import auth_enabled

router = APIRouter()


def _service_changes_allowed() -> None:
    """Installing a service is the owner's act: never over an unauthenticated development API."""
    if not auth_enabled():
        raise HTTPException(status_code=403, detail="not available without the API token")


@router.get("/service")
async def service_status() -> dict[str, Any]:
    from trading_r1.host import daemon

    return await asyncio.to_thread(daemon.status)


@router.post("/service/install")
async def service_install() -> dict[str, Any]:
    from trading_r1.host import daemon

    _service_changes_allowed()
    try:
        copied, note = await asyncio.to_thread(daemon.install_here)
    except (daemon.ServiceError, RuntimeError, ValueError) as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {"installed": True, "copied": copied, "note": note}


@router.delete("/service")
async def service_remove() -> dict[str, bool]:
    from trading_r1.host import daemon

    _service_changes_allowed()
    try:
        await asyncio.to_thread(daemon.remove_here)
    except daemon.ServiceError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return {"removed": True}
