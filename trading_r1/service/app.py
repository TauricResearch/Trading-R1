# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The FastAPI application: the factory that builds it, the bearer-token middleware, the error
handlers, ``/livez`` and ``/version``, and the lifespan that runs the scheduler and the session
sync while the app does. Every other route lives in a router under ``service.routes``;
process-wide state lives in ``service.state``. ``backend`` is the app of the process that owns the
backend (the scheduler, the Telegram listener and the sync run in it); ``app`` serves without them
(the development server, tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from trading_r1.delivery import Outbox
from trading_r1.runtime.session.store import InvalidSessionIdError
from trading_r1.scheduler import JobStore, Scheduler
from trading_r1.service import state
from trading_r1.service.auth import AuthMiddleware
from trading_r1.service.inbound import Inbound
from trading_r1.service.routes import (
    channels,
    credentials,
    jobs,
    models,
    research,
    service,
    sessions,
    settings,
    sync,
)
from trading_r1.service.unattended import execute_job, notify_unrun

_LOG = logging.getLogger(__name__)


def _lifespan(run_scheduler: bool) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The scheduler, and the Telegram listener and the session sync beside it, run while the
    app does when ``run_scheduler`` is set: only in the process that owns the backend."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        inbound: Inbound | None = None
        syncing: asyncio.Task[None] | None = None
        # the stores are opened now: a file that cannot be used fails or is replaced at start
        outbox = Outbox()
        outbox.check()
        state.jobs().check()
        state.SESSIONS.index.check()
        if run_scheduler:
            outbox.recover()  # a message whose outcome the last backend never recorded is unknown
            state.SCHEDULER = Scheduler(JobStore(), execute_job, notify_unrun)
            state.SCHEDULER.start()
            inbound = Inbound(state.SCHEDULER)
            inbound.start()
            try:
                syncing = asyncio.create_task(state.sync().run())
                syncing.add_done_callback(state.log_if_died)
            except Exception:  # noqa: BLE001 (sync trouble never keeps the app from starting)
                _LOG.exception("session sync did not start")
        try:
            yield
        finally:
            if syncing is not None:
                syncing.cancel()
                await asyncio.gather(syncing, return_exceptions=True)
                if state.SYNC is not None:
                    with contextlib.suppress(Exception):
                        await state.SYNC.aclose()
                    state.SYNC = None
            if inbound is not None:
                await inbound.stop()
            if state.SCHEDULER is not None:
                await state.SCHEDULER.stop()
                state.SCHEDULER = None

    return lifespan


async def livez() -> dict[str, str]:
    """Liveness: answers without a token, says nothing about the machine."""
    return {"service": "trading-r1", "status": "ok"}


async def version() -> dict[str, Any]:
    """Who is serving: the product version and the process (what a client checks before it
    trusts a recorded address)."""
    return {"service": "trading-r1", "version": state.API_VERSION, "pid": os.getpid()}


async def _invalid_session_id(_request: Request, exc: InvalidSessionIdError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


async def _invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Say which field is wrong, never the value sent (it may be a secret)."""
    errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


def create_app(*, run_scheduler: bool = False) -> FastAPI:
    app = FastAPI(title="Trading-R1", version=state.API_VERSION, lifespan=_lifespan(run_scheduler))
    app.add_middleware(AuthMiddleware)
    app.add_api_route("/livez", livez, methods=["GET"])
    app.add_api_route("/version", version, methods=["GET"])
    app.add_exception_handler(InvalidSessionIdError, _invalid_session_id)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _invalid_request)  # type: ignore[arg-type]
    for area in (
        models,
        research,
        sessions,
        channels,
        credentials,
        service,
        jobs,
        settings,
        sync,
    ):
        app.include_router(area.router)
    return app


app = create_app()


def backend() -> FastAPI:
    """The app of the process that owns the backend: uvicorn's factory for ``trading-r1 serve``
    and the terminal's own backend."""
    return create_app(run_scheduler=True)
