# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Where reports are saved as files.

One folder serves every way a report becomes files (the terminal's export, the folder
destination): `~/Documents/Trading-R1` when the user has a Documents folder, else `~/Trading-R1`,
or `$TR1_REPORTS_DIR`, or `<state>/reports` when the state directory was moved. A name the owner
types is made inside it unless it is a full path (`~` is the home folder), and a folder that does
not exist yet is created. Each report is a subdirectory named by date, time and a name (a
session id, a job's name); the same minute and name replace the earlier files.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from trading_r1.state.paths import state_dir

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ReportsDirError(ValueError):
    """The folder name cannot be used; the message says why."""


def default_dir() -> Path:
    env = os.environ.get("TR1_REPORTS_DIR")
    if env:
        return Path(env).expanduser()
    if os.environ.get("TR1_HOME"):
        return state_dir() / "reports"
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Trading-R1"


def resolve(text: str | None) -> Path:
    """The folder a typed name stands for: empty is the reports folder, a relative name is made
    inside it, a full path (or one starting with ``~``) is itself."""
    text = (text or "").strip()
    if any(ord(c) < 32 for c in text):
        raise ReportsDirError("a folder name is one line of text")
    if not text:
        return default_dir()
    try:
        path = Path(text).expanduser()
    except RuntimeError as err:  # ~name of a user that does not exist
        raise ReportsDirError(f"{text}: no such home folder") from err
    if path.is_absolute():
        return path
    if ".." in path.parts:
        raise ReportsDirError("a name inside the reports folder cannot contain ..; use a full path")
    return default_dir() / path


def safe_name(text: str) -> str:
    """A name safe to put in a path, from a session id or a job's name."""
    return _UNSAFE.sub("-", text).strip("-._")[:60]


def write_report(root: Path, name: str, files: Mapping[str, bytes]) -> Path:
    """Write ``files`` into a subdirectory of ``root`` (created, for the owner alone, if
    missing); the subdirectory. Raises ``OSError`` when the folder cannot be written."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    base = datetime.now().strftime("%Y-%m-%d-%H%M") + (f"-{name}" if name else "")
    directory, copy = root / base, 1
    while True:  # one directory per report, however close together they come
        try:
            directory.mkdir(mode=0o700)
            break
        except FileExistsError:
            copy += 1
            directory = root / f"{base}-{copy}"
    for filename, data in files.items():
        (directory / Path(filename).name).write_bytes(data)
    return directory
