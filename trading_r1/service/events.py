# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""SSE framing for POST /research (stream=true).

The protocol itself (every event and its fields) is defined once, as models, in
`trading_r1.contract.events`; the TUI's types are generated from them. A frame is
``event: <kind>\\ndata: <json>\\n\\n`` on a versioned ``text/event-stream``, and the terminal
`done` carries the whole trajectory (with the ``session_id``) so a streaming client ends with
the same run artifact a sync client receives: the sync `/research` response is that trajectory
with ``session_id`` merged in.
"""

from __future__ import annotations

import json
from typing import Any

from trading_r1.contract.events import SSE_VERSION, validate

__all__ = ["SSE_VERSION", "sse_frame"]


def sse_frame(event: str, data: dict[str, Any]) -> str:
    """Serialize one SSE frame, after checking it against its event model. An unknown event or
    a payload the model refuses is drift between the run and the protocol, so it raises here."""
    validate(event, data)
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
