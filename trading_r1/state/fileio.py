# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Atomic file writes and private JSON reads shared by the durable stores."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: a unique temp file in the same directory (so
    concurrent writers never share one), then ``os.replace``: readers see the old file or the
    new one, never half of either. Parents are created; the temp is removed on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_json(path: Path, data: Any, *, private: bool = False, ensure_ascii: bool = True) -> None:
    """Write ``data`` to ``path`` as indented JSON, atomically. ``private`` leaves the file
    readable by the owner alone."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=ensure_ascii) + "\n")
    if private:
        path.chmod(0o600)


MISSING = object()  # what read_private_json returns when there is no file


class PrivateFileError(Exception):
    """A file that must be the owner's own private file is not, or does not parse."""


def read_private_json(path: Path, *, max_bytes: int = 1_000_000) -> Any:
    """The JSON in the owner's own private regular file, or ``MISSING`` when there is no such file
    (a file that holds `null` is not missing).

    Opened without following a link or waiting on a pipe, and checked on the open descriptor
    (so nothing can change between the check and the read): a regular file, the user's, not
    accessible to group or others. Anything else, or text that does not parse, raises
    ``PrivateFileError`` saying what to do; callers never treat that as an empty file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return MISSING
    except OSError as err:
        raise PrivateFileError(f"{path} cannot be opened ({err.strerror}); remove it") from err
    with os.fdopen(fd, "rb") as fh:
        info = os.fstat(fh.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PrivateFileError(f"{path} is not your own file; remove it")
        if info.st_mode & 0o077:
            raise PrivateFileError(f"{path} is open to other users; run: chmod 600 {path}")
        try:
            return json.loads(fh.read(max_bytes).decode("utf-8"))
        except ValueError as err:
            raise PrivateFileError(f"{path} is not valid JSON; fix or remove it") from err


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """An exclusive advisory lock on ``path`` (created for the owner alone), held for the block
    and shared by every process that locks the same file."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing drops the lock
