# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Credential routes: the model keys the application looks for and whether each is set
(``GET /credentials``), and write-only storage of a key, password or token
(``PUT``/``DELETE /credentials/{name}``). A value is never returned; a name that a channel holds
as its secret is changed by editing the channel.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

router = APIRouter()


@router.get("/credentials")
async def list_credentials() -> dict[str, Any]:
    """The model keys the application looks for and whether each is set (never a value)."""
    from trading_r1.providers.builtins import model_keys
    from trading_r1.state import credentials

    keys = model_keys()
    state = credentials.status(list(keys))
    return {
        "credentials": [{"name": n, "label": keys[n], **state[n]} for n in keys],
        "problem": credentials.problem(),
    }


class CredentialPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


@router.put("/credentials/{name}")
async def put_credential(name: str, req: CredentialPut) -> dict[str, Any]:
    """Store a key, password or token (write-only). A value in the environment still wins."""
    from trading_r1.delivery.manage import credential_users
    from trading_r1.state import credentials

    if users := credential_users(name):
        raise HTTPException(
            status_code=409,
            detail=f"{name} is the secret of channel {', '.join(users)}; "
            "change it by editing the channel",
        )
    try:
        await asyncio.to_thread(credentials.set_value, name, req.value)
    except credentials.CredentialsError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"name": name, **credentials.status([name])[name]}


@router.delete("/credentials/{name}")
async def delete_credential(name: str) -> dict[str, Any]:
    from trading_r1.state import credentials

    try:
        removed = await asyncio.to_thread(credentials.delete, name)
    except credentials.CredentialsError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return {"name": name, "removed": removed, **credentials.status([name])[name]}
