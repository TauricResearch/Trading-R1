# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""A folder on this machine: each message becomes a directory of its files.

The folder is the reports folder (`reports_dir`) unless the owner names another. A synced folder
(a cloud drive, a phone sync) makes this a delivery channel that needs no account.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trading_r1.delivery.base import Channel, ChannelError, Field, Kind, Receipt
from trading_r1.delivery.message import Message
from trading_r1.state import reports_dir


class FolderChannel(Channel):
    name = "folder"

    def __init__(self, cfg: Mapping[str, Any], env: Mapping[str, str]) -> None:
        try:
            self.root = reports_dir.resolve(str(cfg.get("path") or ""))
        except reports_dir.ReportsDirError as err:
            raise ChannelError(f"folder: {err}") from err
        if self.root.exists() and not self.root.is_dir():
            raise ChannelError(f"folder: {self.root} is not a folder")
        self.saved: Path | None = None  # the directory of the last message

    def address(self) -> str:
        return f"folder {self.root}"

    def _write(self, message: Message) -> Receipt:
        # a message with no files (a notice) is kept as its text
        files = {a.name: a.data for a in message.attachments} or {
            "trading-r1-notice.txt": f"{message.subject}\n\n{message.text}\n".encode()
        }
        try:
            self.saved = reports_dir.write_report(
                self.root, reports_dir.safe_name(message.name), files
            )
        except OSError as err:
            return Receipt("failed", f"{err.filename}: {err.strerror or err}"[:300])
        return Receipt("sent", f"{self.saved.name}: {', '.join(files)}")

    async def send(self, message: Message) -> Receipt:
        return await asyncio.to_thread(self._write, message)


KIND = Kind(
    name="folder",
    label="Folder",
    summary="Reports saved as files in a folder on this machine; point a sync tool at it to "
    "reach other devices.",
    fields=(
        Field(
            "path",
            "Folder",
            "path",
            help="Empty: the reports folder. A name is made inside it; a full path or ~/… is "
            "used as it is. It is created if it does not exist.",
            example="Documents/Trading-R1",
        ),
    ),
    make=FolderChannel,
)
