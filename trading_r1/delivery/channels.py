# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The channels the owner configured, built and ready to send.

Where a message goes is configuration written by the owner (`config.py`), never chosen by the
model. Secrets are looked up by name in the environment and the credentials store, so the file
holds none. Every send ends as ``sent`` (the channel confirmed it), ``failed`` (it certainly
did not go) or ``unknown`` (the request went out and the answer did not come back: it may have
been delivered, and is never retried by itself).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_r1.delivery.base import Channel, ChannelError
from trading_r1.delivery.config import ConfigError, read_config
from trading_r1.delivery.kinds import KINDS
from trading_r1.state import credentials


def build_channels(
    env: Mapping[str, str] | None = None,
    config: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Channel], dict[str, str]]:
    """The channels the owner configured, and why any that is configured is unusable. A settings
    file that cannot be trusted is reported under the name ``settings``."""
    env = credentials.overlay() if env is None else env
    problems: dict[str, str] = {}
    if config is None:
        try:
            config = read_config()
        except ConfigError as err:
            return {}, {"settings": str(err)}
    channels: dict[str, Channel] = {}
    for name, cfg in config.items():
        kind = KINDS.get(str(cfg.get("kind")))
        if kind is None:
            problems[name] = f"unknown kind {cfg.get('kind')!r}"
            continue
        try:
            values = kind.validate({k: v for k, v in cfg.items() if k != "kind"})
            channels[name] = kind.make(values, env)
        except ChannelError as err:
            problems[name] = str(err)
    return channels, problems
