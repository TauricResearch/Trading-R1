# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Credentials the owner gives the application: API keys, passwords and tokens.

A value comes from the environment, else from the store (``credentials.json`` in the private state
directory). ``overlay()`` is the environment with the store's values added where the environment
has none, and is what code that looks for a key by name is given, so a stored key is found
wherever an exported one is. The store is the owner's own regular file and no one else's: it is
opened without following links and checked on the open descriptor, and a store that is corrupt or
open to others is an error, never treated as empty and written over.

Plaintext at mode 0600 protects against other users of the machine. It does not protect against the
owner's own processes, root, or a backup or sync tool that copies the state directory.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Iterator, Mapping
from typing import Any

from trading_r1.state.fileio import (
    MISSING,
    PrivateFileError,
    file_lock,
    read_private_json,
    write_json,
)
from trading_r1.state.paths import private_state_dir, state_dir

log = logging.getLogger(__name__)
FILE = "credentials.json"
LOCK = "credentials.lock"
NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MAX_VALUE = 8192

_cache: tuple[tuple[Any, ...], dict[str, str]] | None = None
_warned: set[str] = set()


class CredentialsError(Exception):
    """The credentials store (``credentials.json``) cannot be trusted or written, or a name or
    value is not valid; the message says what to do."""


def path():  # noqa: ANN201
    return state_dir() / FILE


def valid_name(name: str) -> bool:
    return NAME.fullmatch(name) is not None


def _load() -> dict[str, str]:
    """The store's values; {} when there is no store. Raises ``CredentialsError`` for a store
    that is not the owner's own private regular file or does not parse."""
    try:
        data = read_private_json(path())
    except PrivateFileError as err:
        raise CredentialsError(str(err)) from err
    if data is MISSING:
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise CredentialsError(f"{path()} must map names to text; fix or remove it")
    return data


def read_store() -> dict[str, str]:
    """The stored values, cached while the file is unchanged (it is read on every key lookup)."""
    global _cache
    try:
        info = os.lstat(path())
    except FileNotFoundError:
        _cache = None
        return {}
    key = (
        info.st_ino,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_size,
        info.st_mode,
        info.st_uid,
        str(path()),
    )
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    values = _load()
    _cache = (key, values)
    return values


def problem() -> str | None:
    """Why the store cannot be used, or None."""
    try:
        read_store()
    except CredentialsError as err:
        return str(err)
    return None


def overlay(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``env`` (default the process environment) with the store's values where it has none. An
    unusable store is reported once in the log and contributes nothing."""
    base = dict(os.environ if env is None else env)
    try:
        stored = read_store()
    except CredentialsError as err:
        if str(err) not in _warned:
            _warned.add(str(err))
            log.warning("credentials not used: %s", err)
        return base
    for name, value in stored.items():
        if not base.get(name):
            base[name] = value
    return base


def status(names: list[str], env: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Whether each name is set and where from, never the value."""
    env = os.environ if env is None else env
    try:
        stored = read_store()
    except CredentialsError:
        stored = {}
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        source = "env" if env.get(name) else "store" if stored.get(name) else None
        out[name] = {"configured": source is not None, "source": source}
        if source == "env" and stored.get(name):
            out[name]["shadows_store"] = True  # the environment wins over the stored value
    return out


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    with file_lock(private_state_dir() / LOCK):
        yield


def _write(values: dict[str, str]) -> None:
    global _cache
    write_json(path(), values, private=True)
    _cache = None


def set_value(name: str, value: str) -> None:
    set_values({name: value})


def set_values(new: Mapping[str, str]) -> None:
    """Store these values, all or none."""
    for name, value in new.items():
        if not valid_name(name):
            raise CredentialsError("a name is capital letters, digits and underscores, e.g. MY_KEY")
        if not value or len(value) > MAX_VALUE or any(c in value for c in "\r\n\0"):
            raise CredentialsError(f"{name}: a value is one line of text, up to 8192 characters")
    with _locked():
        values = read_store()  # a bad store stops the write; it is never overwritten
        _write({**values, **new})


def delete(name: str) -> bool:
    """Remove a stored value; False when it was not stored."""
    with _locked():
        values = dict(read_store())
        if name not in values:
            return False
        del values[name]
        _write(values)
        return True
