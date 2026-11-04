# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""User settings: the persisted preferences under ``~/.trading-r1``.

The sticky backbone (the last one chosen in the model picker) and the preferences (which keys,
and what each accepts, is the service's table). A preference that was never chosen is absent, and
the runtime config or the built-in default decides. A file that cannot be used reads as empty and
``read`` says why; a write keeps a copy of it and replaces it. Writes are atomic. The file
is ``$TR1_SETTINGS_FILE`` or ``~/.trading-r1/settings.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from trading_r1.state.fileio import write_json
from trading_r1.state.paths import state_dir


def default_settings_file() -> Path:
    """Default settings file, overridable with ``TR1_SETTINGS_FILE`` ."""
    env = os.environ.get("TR1_SETTINGS_FILE")
    if env:
        return Path(env).expanduser()
    return state_dir() / "settings.json"


# What a settings file written over an unreadable one starts from: that file may have said no.
_AFTER_UNREADABLE = {"sync": False}


class SettingsStore:
    """File-backed user settings. ``path`` defaults to the user settings file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else default_settings_file()

    def read(self) -> tuple[dict[str, Any], str | None]:
        """The settings dict and, when the file exists but cannot be used, why. No file is not a
        problem: nothing has been chosen yet."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}, None
        except (OSError, UnicodeError, json.JSONDecodeError) as err:
            return {}, f"{self._path} cannot be read ({type(err).__name__})"
        if not isinstance(data, dict):
            return {}, f"{self._path} does not hold settings"
        return data, None

    def get(self) -> dict[str, Any]:
        """The whole settings dict; an unusable file reads as ``{}`` (``read`` says why)."""
        return self.read()[0]

    def get_backbone(self) -> str | None:
        """The persisted backbone name, or ``None`` if unset (absent or ``null``)."""
        v = self.get().get("backbone")
        return v if isinstance(v, str) and v else None

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Write ``changes`` in one go (a ``None`` value forgets that key); returns the settings.
        The caller has checked them."""
        data, problem = self.read()
        if problem is not None:  # a copy kept for the user; sending stays off until they turn it on
            with contextlib.suppress(OSError):
                aside = self._path.with_name(f"{self._path.name}.unreadable")
                shutil.copyfile(self._path, aside)
            data = dict(_AFTER_UNREADABLE)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        self._write(data)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        write_json(self._path, data, ensure_ascii=False)


def persisted_backbone() -> str | None:
    """The user's sticky backbone from the settings file, or ``None``. Fail-open: used by the
    default-backbone cascade so a direct/headless caller honors ``/model`` too. The store's
    *location* is a process-env concern (``TR1_SETTINGS_FILE``), independent of the selection
    ``env`` threaded through the cascade; tests isolate it via ``TR1_SETTINGS_FILE`` (conftest)."""
    return SettingsStore().get_backbone()
