# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Where Trading-R1 keeps its state: `$TR1_HOME`, else `~/.trading-r1`."""

from __future__ import annotations

import os
from pathlib import Path


class StateDirNotPrivateError(RuntimeError):
    """The state directory is open to other users; sessions, secrets and the token are not kept
    there."""


def state_dir() -> Path:
    env = os.environ.get("TR1_HOME")
    return Path(env).expanduser().resolve() if env else Path.home() / ".trading-r1"


def private_state_dir() -> Path:
    """The state directory, created if missing, and checked to be the user's alone. The default
    directory is tightened to owner-only; a directory the user pointed ``TR1_HOME`` at is left as
    it is, and refused if others can enter it."""
    directory = state_dir()
    custom = bool(os.environ.get("TR1_HOME"))
    existed = directory.is_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.stat()
    if info.st_uid != os.getuid():
        raise StateDirNotPrivateError(f"{directory} belongs to another user")
    if info.st_mode & 0o077:
        if custom and existed:
            raise StateDirNotPrivateError(
                f"{directory} is open to other users; run: chmod 700 {directory}"
            )
        directory.chmod(0o700)
    return directory
