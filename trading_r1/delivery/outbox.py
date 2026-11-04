# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The outbox: every send is written down before it is made.

A row is ``attempting`` before the request goes out and is settled after it. A row still
``attempting`` when the backend starts again is one whose outcome nobody saw, so it becomes
``unknown``. Each send has a key (a run and a channel): asking again for a key that has a row
returns that row instead of sending a second time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading_r1.delivery.base import Channel, Receipt
from trading_r1.delivery.message import Message
from trading_r1.state.paths import state_dir
from trading_r1.state.sqlite import open_store

_LOG = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL,
    address TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_parts (
    address TEXT NOT NULL,
    part INTEGER NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (address, part)
)
"""


@dataclass(frozen=True)
class Outcome:
    channel: str
    address: str
    status: str
    detail: str = ""
    repeated: bool = False  # the key already had a row: nothing was sent now


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _summary(message: Message) -> str:
    """What the row records of the message: its size and its files, not their contents."""
    return json.dumps(
        {
            "text_chars": len(message.text),
            "attachments": [
                {"name": a.name, "type": a.content_type, "bytes": len(a.data)}
                for a in message.attachments
            ],
        }
    )


class Outbox:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or state_dir() / "outbox.sqlite"

    def _connect(self) -> sqlite3.Connection:
        return open_store(self._path, _SCHEMA, keep=("outbox", "sent_parts"))

    def check(self) -> None:
        """Open the store now, so a file that cannot be used fails here and not at a send."""
        self._connect().close()

    def recover(self) -> int:
        """Rows whose outcome was never recorded become ``unknown``; how many."""
        with self._connect() as db:
            cur = db.execute(
                "UPDATE outbox SET status='unknown', detail='the backend stopped before the "
                "outcome was recorded', updated=? WHERE status='attempting'",
                (_now(),),
            )
            return cur.rowcount

    def begin(self, key: str, channel: str, address: str, message: Message) -> Outcome:
        """Write the row ``attempting``. A key that already has a row returns that row, marked
        ``repeated``."""
        with self._connect() as db:
            cur = db.execute(
                "INSERT OR IGNORE INTO outbox(key, channel, address, subject, message, "
                "status, created, updated) VALUES (?,?,?,?,?,'attempting',?,?)",
                (key, channel, address, message.subject, _summary(message), _now(), _now()),
            )
        if cur.rowcount:
            return Outcome(channel, address, "attempting")
        earlier = self.row(key)
        assert earlier is not None  # the insert was ignored because the row exists
        return earlier

    def forget_failed(self, keys: list[str]) -> None:
        """Let these sends be tried again, if they failed. Nothing else about a key changes: a
        message that may have gone out is never sent twice."""
        with self._connect() as db:
            db.executemany(
                "DELETE FROM outbox WHERE key=? AND status='failed'", [(k,) for k in keys]
            )

    def settle(self, key: str, receipt: Receipt) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE outbox SET status=?, detail=?, updated=? WHERE key=?",
                (receipt.status, receipt.detail, _now(), key),
            )
            db.executemany(
                "INSERT OR REPLACE INTO sent_parts(address, part, key) "
                "SELECT address, ?, key FROM outbox WHERE key=?",
                [(part, key) for part in receipt.ids],
            )

    def sent_as(self, address: str, part: int) -> str | None:
        """The key of the send a message at this address belongs to, if it was recorded."""
        with self._connect() as db:
            found = db.execute(
                "SELECT key FROM sent_parts WHERE address=? AND part=?", (address, part)
            ).fetchone()
        return found[0] if found else None

    def row(self, key: str) -> Outcome | None:
        with self._connect() as db:
            found = db.execute(
                "SELECT channel, address, status, detail FROM outbox WHERE key=?", (key,)
            ).fetchone()
        return Outcome(*found, repeated=True) if found else None


async def deliver(
    outbox: Outbox,
    channels: dict[str, Channel],
    names: list[str],
    message: Message,
    key: str,
    unusable: Mapping[str, str] | None = None,
) -> list[Outcome]:
    """Send ``message`` through each named channel, once per ``key``. A name with no usable
    channel, and a send the outbox cannot record (so none is made), is a ``failed`` outcome, not
    an error; ``unusable`` gives the reason a channel is not among ``channels``."""
    outcomes: list[Outcome] = []
    for name in names:
        channel = channels.get(name)
        if channel is None:
            outcomes.append(
                Outcome(
                    name,
                    "",
                    "failed",
                    (unusable or {}).get(name) or "the channel is not configured",
                )
            )
            continue
        row_key = f"{key}:{name}"
        # the database is blocking I/O: off the event loop
        try:
            begun = await asyncio.to_thread(outbox.begin, row_key, name, channel.address(), message)
        except Exception:  # noqa: BLE001 (nothing was sent: say so, and why in the log)
            _LOG.exception("the outbox could not record the send through %s", name)
            outcomes.append(
                Outcome(name, "", "failed", "the outbox could not be written; not sent")
            )
            continue
        if begun.repeated:
            outcomes.append(begun)
            continue
        try:
            receipt = await channel.send(message)
        except Exception as err:  # noqa: BLE001 (whatever happened, it is on the record)
            _LOG.exception("sending through %s ended in an error", name)
            receipt = Receipt("unknown", f"{type(err).__name__} while sending")
        try:
            await asyncio.to_thread(outbox.settle, row_key, receipt)
        except Exception:  # noqa: BLE001 (the send happened; its outcome is still returned)
            _LOG.exception("the outbox could not record the outcome of the send through %s", name)
        outcomes.append(Outcome(name, channel.address(), receipt.status, receipt.detail))
    return outcomes
