# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Changing the channels the owner configured: save one (with the secrets it names) and remove
one. The HTTP API and the command line both go through here, so they refuse the same things: a
credential name that is a model key, one that already holds another channel's secret, a kind
change or a removal that would leave a scheduled job without its channel.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_r1.delivery.base import ChannelError
from trading_r1.delivery.config import ConfigError, change, read_config, valid_name
from trading_r1.delivery.kinds import KINDS
from trading_r1.providers.builtins import model_key_names
from trading_r1.scheduler import JobStore
from trading_r1.state import credentials


class NoSuchChannelError(ChannelError):
    """No channel has this name."""


class ChannelInUseError(ChannelError):
    """Scheduled jobs send to the channel, so the change would break them."""


def credential_users(name: str, *, except_channel: str | None = None) -> list[str]:
    """The channels whose settings name this credential as a secret."""
    try:
        config = read_config()
    except ConfigError:
        return []
    users = []
    for channel, cfg in config.items():
        kind = KINDS.get(str(cfg.get("kind") or channel))
        if (
            channel != except_channel
            and kind
            and any(cfg.get(f.name) == name for f in kind.secret_fields())
        ):
            users.append(channel)
    return users


def refuse_model_key(field: str, credential: str) -> None:
    """A channel's secret has a name of its own, never a model key's."""
    if credential in model_key_names():
        raise ChannelError(f"{field}: {credential} is a model key; give this secret its own name")


def _jobs_sending_to(name: str) -> list[str]:
    return [j.name for j in JobStore().list() if name in j.deliver]


def save_channel(
    name: str, kind_name: str, settings: Mapping[str, Any], secrets: Mapping[str, str]
) -> dict[str, Any]:
    """Create or replace the channel ``name``: ``settings`` are checked against the kind, and each
    ``secrets`` entry (secret field to value) is stored under the credential name the settings give
    that field. Nothing is written until both files are known to be sound. Returns the entry
    saved. Raises ``ChannelError`` (``ChannelInUseError`` for a kind change that would break a
    job), ``CredentialsError`` or ``ConfigError``."""
    kind = KINDS.get(kind_name)
    if kind is None:
        raise ChannelError(f"unknown kind {kind_name!r}")
    if not valid_name(name):
        raise ChannelError("a name is lowercase letters, digits, - and _")
    values = kind.validate(settings)
    unknown = set(secrets) - {f.name for f in kind.secret_fields()}
    if unknown:
        raise ChannelError(f"not a secret of {kind.name}: {', '.join(sorted(unknown))}")
    for f in kind.secret_fields():
        if f.name in values:
            refuse_model_key(f.name, values[f.name])
    for field in secrets:
        if field not in values:
            raise ChannelError(f"{field}: name the credential in settings first")
        if users := credential_users(values[field], except_channel=name):
            raise ChannelError(
                f"{field}: {values[field]} already holds the secret of channel "
                f"{', '.join(users)}; give this one its own name"
            )
    existing = read_config().get(name)
    credentials.read_store()
    if existing and existing.get("kind") != kind.name and (users := _jobs_sending_to(name)):
        raise ChannelInUseError(
            f"scheduled jobs send to it: {', '.join(users)}; it cannot become a {kind.name} channel"
        )
    for field, value in secrets.items():
        credentials.set_value(values[field], value)
    entry = {"kind": kind.name, **values}
    change(lambda channels: channels.__setitem__(name, entry))
    return entry


def remove_channel(name: str, *, force: bool = False) -> list[str]:
    """Remove the channel ``name``; the names of the scheduled jobs that listed it. Refused with
    ``ChannelInUseError`` while a job lists it, unless ``force``."""
    if name not in read_config():
        raise NoSuchChannelError("no such channel")
    users = _jobs_sending_to(name)
    if users and not force:
        raise ChannelInUseError(f"scheduled jobs send to it: {', '.join(users)}")
    change(lambda channels: channels.pop(name, None))
    return users
