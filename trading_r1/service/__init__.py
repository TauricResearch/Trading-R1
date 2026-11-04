# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""HTTP+SSE service: the harness over a versioned API.

`POST /research` (sync JSON, or SSE when stream=true) is the core; alongside it:
`POST /answer` (reply to ask_user), `POST /compact` (session summarization), `GET /sessions`
+ `/sessions/{id}` (resume), `GET /status`, `GET /models`, `GET /health`. The agent
runs async per request with its own cache/registry (per-request isolation); the
same trajectory shape backs both the sync body and the streamed `done` event.

Run: ``uvicorn trading_r1.service.app:app`` (the FastAPI object lives in the
``app`` submodule and is not re-exported here, to keep the submodule importable).
"""
