# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The channels the owner set up: named instances of a kind, in `channels.json`.

The file is `{"version": 1, "channels": {name: {"kind": ..., ...settings}}}`. It is the owner's
private file; one that is open to others, corrupt or of an unknown format is an error, never read
as empty and never written over. Changes are made under a lock shared by every process.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from typing import Any

from trading_r1.state.fileio import (
    MISSING,
    PrivateFileError,
    file_lock,
    read_private_json,
    write_json,
)
from trading_r1.state.paths import private_state_dir, state_dir

FILE = "channels.json"
LOCK = "channels.lock"
VERSION = 1
NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class ConfigError(Exception):
    """The channels file (``channels.json``) cannot be trusted, or a change to it is not valid."""


def path():  # noqa: ANN201
    return state_dir() / FILE


def read_config() -> dict[str, dict[str, Any]]:
    """The instances, each with its `kind`. Raises ``ConfigError`` for a file that cannot be
    trusted (nothing is sent anywhere from a file another user could have written)."""
    try:
        data = read_private_json(path())
    except PrivateFileError as err:
        raise ConfigError(str(err)) from err
    if data is MISSING:
        return {}
    if isinstance(data, dict) and data.get("version") == VERSION:
        channels = data.get("channels")
        if isinstance(channels, dict) and all(isinstance(v, dict) for v in channels.values()):
            return {k: dict(v) for k, v in channels.items()}
    raise ConfigError(f"{path()} has a format this version does not know; fix or remove it")


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    with file_lock(private_state_dir() / LOCK):
        yield


def change(edit: Callable[[dict[str, dict[str, Any]]], None]) -> None:
    """Read, apply ``edit`` and write, all under the lock. A file that cannot be trusted stops
    the change."""
    with _locked():
        channels = read_config()
        edit(channels)
        write_json(path(), {"version": VERSION, "channels": channels}, private=True)


def valid_name(name: str) -> bool:
    return NAME.fullmatch(name) is not None
