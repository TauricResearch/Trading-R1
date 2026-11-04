# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""What the user can set: each preference's check and default, and the values in effect."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from trading_r1.runtime.modes import DEFAULT_MODE, MODES
from trading_r1.state.runtime_config import RuntimeConfig, load_runtime_config
from trading_r1.state.settings import SettingsStore

SETTINGS = SettingsStore()


class Preference(NamedTuple):
    """One preference: what a value must satisfy (``validate`` returns it, or raises
    ``ValueError`` saying what is expected) and what applies when the user never chose."""

    validate: Callable[[Any], Any]
    default: Callable[[RuntimeConfig], Any]


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected true or false")
    return value


def _one_of(choices: Iterable[str]) -> Callable[[Any], str]:
    allowed = list(choices)

    def check(value: Any) -> str:
        if value not in allowed:
            raise ValueError(f"expected one of {sorted(allowed)}")
        return str(value)

    return check


def _always(value: Any) -> Callable[[RuntimeConfig], Any]:
    return lambda _cfg: value


TOOL_DETAILS = ("brief", "full")  # how much of a tool call's arguments the transcript shows
SAVED_ALSO = ("markdown", "html")  # files a report may be saved as besides the PDF
# The preferences the user can set; the terminal's settings dialog lists the same keys.
PREFERENCES: dict[str, Preference] = {
    "verify": Preference(_boolean, lambda cfg: cfg.verify.enabled),
    "grounding": Preference(_boolean, lambda cfg: cfg.grounding.enabled),
    "auto_compact": Preference(_boolean, lambda cfg: cfg.recovery.auto_compact),
    "show_reasoning": Preference(_boolean, _always(True)),
    "default_mode": Preference(_one_of(MODES), _always(DEFAULT_MODE)),
    "tool_details": Preference(_one_of(TOOL_DETAILS), _always("brief")),
    "side_panel": Preference(_boolean, _always(True)),
    "sync": Preference(_boolean, _always(True)),  # copy sessions to the Tauric archive
    **{
        f"report_{f}": Preference(_boolean, _always(f == "markdown"))  # markdown on, the page off
        for f in SAVED_ALSO
    },
}


def current() -> dict[str, Any]:
    """The preferences in effect: the user's choice (when it still passes its check), else the
    runtime config, else the built-in default."""
    cfg = load_runtime_config()
    own, problem = SETTINGS.read()
    out = {}
    for key, pref in PREFERENCES.items():
        try:
            out[key] = pref.validate(own[key])
        except (KeyError, ValueError):
            out[key] = pref.default(cfg)
    # copying to the archive needs a yes or no default that nothing contradicts: a file that
    # cannot be read, or a value that makes no sense, may have been a no
    if problem is not None or ("sync" in own and own["sync"] is not out["sync"]):
        out["sync"] = False
    return out


def check(changes: dict[str, Any]) -> dict[str, Any]:
    """The preference changes (a ``None`` forgets one), each checked. Raises ``ValueError``;
    nothing is written here."""
    checked: dict[str, Any] = {}
    for key, value in changes.items():
        try:
            checked[key] = None if value is None else PREFERENCES[key].validate(value)
        except ValueError as err:
            raise ValueError(f"{key}: {err}") from err
    return checked


def report_formats() -> tuple[str, ...]:
    """The files a report is made as: the PDF, which a chat or a mailbox gets, and what the user
    chose to save beside it."""
    prefs = current()
    return ("pdf", *(f for f in SAVED_ALSO if prefs[f"report_{f}"]))


def run_config() -> RuntimeConfig:
    """The runtime config a run starts with: the file and env, with the user's settings on top."""
    cfg = load_runtime_config()
    prefs = current()
    return cfg.model_copy(
        update={
            "verify": cfg.verify.model_copy(update={"enabled": prefs["verify"]}),
            "grounding": cfg.grounding.model_copy(update={"enabled": prefs["grounding"]}),
            "recovery": cfg.recovery.model_copy(update={"auto_compact": prefs["auto_compact"]}),
        }
    )
