# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Where logging is configured: the operator's trail of operational warnings (a
recovery backoff, an unreadable config).

The standalone server keeps them on stderr. In the TUI the Bun UI owns the terminal while the
backend runs in-process, and a stray stderr line would corrupt the display, so the TUI routes them
to a file.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from trading_r1.state.paths import state_dir

_LEVEL_ENV = "TR1_LOG_LEVEL"  # e.g. DEBUG / INFO / WARNING (default WARNING)
_FILE_ENV = "TR1_LOG_FILE"
_LOG_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3
_configured = False


class _PrivateRotatingFile(logging.handlers.RotatingFileHandler):
    """A rotating log file that only its owner can read, the rotated ones included."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)


def default_log_file() -> Path:
    """Where TUI-mode logs land: beside the durable sessions, overridable via env."""
    env = os.environ.get(_FILE_ENV)
    return Path(env) if env else state_dir() / "trading_r1.log"


def configure_logging(*, to_file: bool = False) -> None:
    """Attach one handler to the ``trading_r1`` logger and stop it propagating to the
    root, so our records go exactly one place (a file in TUI mode, else stderr) and never
    fall through to the last-resort stderr handler that would garble the TUI. Idempotent."""
    global _configured
    if _configured:
        return
    if to_file:
        path = default_log_file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler: logging.Handler = _PrivateRotatingFile(
            path, maxBytes=_LOG_BYTES, backupCount=_LOG_BACKUPS
        )
    else:
        handler = logging.StreamHandler()  # stderr
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )

    level = os.environ.get(_LEVEL_ENV, "WARNING").upper()
    # In file mode (the TUI, which owns the terminal) also capture uvicorn: it installs its
    # own stderr handlers, so redirecting only our logger would still let the in-process
    # server garble the display. Replacing each logger's handlers, plus log_config=None on
    # the server, keeps stderr clean.
    names = ["trading_r1"]
    if to_file:
        names += ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for name in names:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers = [handler]
        logger.propagate = False  # never fall through to the last-resort stderr handler
    _configured = True
