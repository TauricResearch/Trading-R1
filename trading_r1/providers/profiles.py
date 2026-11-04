# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Model profiles: the config that selects *which* backbone a call uses.

A profile names a model, an OpenAI-compatible endpoint, the credential to use,
generation defaults, and an optional quirk adapter. Profiles come from built-in
defaults, then an optional JSON file merged per profile (this machine's
``~/.trading-r1/providers.json``, ``$TR1_PROVIDERS_CONFIG``), then
``TR1_<NAME>_BASE_URL`` / ``_MODEL`` / ``_API_KEY`` env.
Where a deployment serves its models is configuration: the built-ins expect a vLLM on loopback
``:8000``, and a machine that serves elsewhere says so in its file or env.

A backbone is chosen by ``TR1_PROFILE``, else the user's persisted ``/model`` choice, else the
file ``"default"``; when none applies, ``default_backbone_name`` picks the first provider the user
has supplied (``builtins.py``), else none.

Precedence matches ``state/runtime_config.py`` (defaults < file < env). A missing file is not an
error; an unreadable one, or an explicitly *named* profile that is unknown, raises
``ProfileSelectionError``, so a typo is surfaced, not swapped for another backbone.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.providers.builtins import builtin_profiles, supplied_provider
from trading_r1.state import credentials
from trading_r1.state.paths import state_dir
from trading_r1.state.settings import persisted_backbone


class Generation(BaseModel):
    """Sampling defaults for a profile. The output budget is the runtime's
    (``generation.max_tokens``), within the endpoint's ``output_limit``."""

    model_config = ConfigDict(extra="forbid")

    temperature: float = 0.7
    top_p: float = 1.0
    presence_penalty: float = 0.0


class DisplayMeta(BaseModel):
    """Presentation metadata for a backbone, *not* wire config. Drives the picker
    header and stable ordering; kept separate from the fields that build a request.
    ``provider_label`` is what the menu shows (``Tauric``, ``OpenAI``, ``Google``, ``vLLM``,
    ``Ollama``); ``kind`` groups hosted vs local; ``order`` is a stable sort key independent
    of readiness so rows never reshuffle between picker openings."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cloud", "local"] = "cloud"
    provider_label: str | None = None  # menu header; falls back to the profile name
    order: int = 100  # stable sort key; lower first


class ModelProfile(BaseModel):
    """A single backbone target. ``base_url=None`` uses the OpenAI default
    endpoint; set it to e.g. ``http://127.0.0.1:8000/v1`` for a local vLLM.
    The API key is resolved at client-construction time (never stored)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None  # the variable that holds its key; None: it takes none
    api_key_literal: str | None = None  # e.g. "EMPTY" for an open vLLM
    timeout_s: float = 600.0
    # The endpoint's per-request output limit (e.g. a hosted API's ceiling): every call's
    # max_tokens stays within it, escalation on truncation included. None = no limit stated.
    output_limit: int | None = None
    # The most input tokens one request may carry. A hosted model's is stated here; a server
    # that reports its own (vLLM, Ollama, Gemini) is asked (providers/discovery.py); None leaves
    # the runtime's default.
    context_window: int | None = None
    generation: Generation = Field(default_factory=Generation)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    adapter: str | None = None  # quirk-adapter name (see providers/adapters)
    # The wire protocol the loop uses (providers/base.Provider): "chat" =
    # OpenAI-compatible Chat Completions (vLLM/Qwen, Gemini-compat); "openai-responses"
    # = the OpenAI Responses API (native reasoning summaries + stateless threading);
    # "gemini" = the native google-genai SDK. Selects the provider via provider_for().
    provider: Literal["chat", "openai-responses", "gemini", "anthropic"] = "chat"
    # Role in the harness: a "backbone" drives the ReAct loop (and is switchable via
    # /model); a "reader" is the page-reading model and is never a loop backbone.
    role: Literal["backbone", "reader"] = "backbone"
    # The reader (page-extraction) profile to pair with this backbone. A run needs one: a
    # backbone that declares none is refused. Cloud backbones name a cloud reader so a run never
    # depends on the local reader being served; some backbones read their own pages (gemini).
    reader_profile: str | None = None
    # Presentation metadata (picker header, grouping, order). Backbones set it; readers
    # leave it None (they are never shown as a selectable option).
    display: DisplayMeta | None = None

    def capped(self, max_tokens: int) -> int:
        """``max_tokens`` within this endpoint's per-request output limit, when it states one."""
        return min(max_tokens, self.output_limit) if self.output_limit else max_tokens

    def resolve_api_key(self, env: Mapping[str, str] | None = None) -> str:
        """Resolve the credential: explicit literal wins, else the env var,
        else "" (the client falls back to a placeholder for keyless vLLM)."""
        if self.api_key_literal is not None:
            return self.api_key_literal
        if self.api_key_env is None:
            return ""
        return (env if env is not None else credentials.overlay()).get(self.api_key_env, "")


_BUILTIN_PROFILES = builtin_profiles()
LOCAL_READERS = {"vllm-reader": "VLLM", "ollama-reader": "OLLAMA"}


class ProfileSelectionError(Exception):
    """No usable backbone was named: an explicit selection (``TR1_PROFILE``/file ``"default"``)
    or a requested name that does not exist, or no selection at all. Raised instead of silently
    substituting a backbone the user did not choose: a typo surfaces rather than being read as
    "unselected". Also raised for a providers file that cannot be read or holds an invalid
    profile, so the user is told rather than silently given the defaults. Callers convert it to
    CLI exit 2 / HTTP 400; the message stays specific."""


def _read_file(path: Path) -> dict[str, Any]:
    """The providers file as a dict; an unreadable or invalid file is a ProfileSelectionError."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        raise ProfileSelectionError(f"cannot read {path}: {err}") from err
    if not isinstance(data, dict):
        raise ProfileSelectionError(f"{path} must hold a JSON object")
    return data


def default_providers_file() -> Path:
    """This machine's providers file: ``TR1_PROVIDERS_CONFIG`` if set, else
    ``~/.trading-r1/providers.json`` (beside the sessions)."""
    env = os.environ.get("TR1_PROVIDERS_CONFIG")
    return Path(env).expanduser() if env else state_dir() / "providers.json"


def _layers(path: str | Path | None) -> list[dict[str, Any]]:
    """The providers file: an explicit ``path``, else
    this machine's."""
    paths = [Path(path) if path is not None else default_providers_file()]
    return [_read_file(p) for p in paths if p.exists()]


def load_profiles(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, ModelProfile]:
    """Load all profiles: built-ins merged with each providers file's ``profiles`` (later
    wins, per profile and per field), then env overrides. An unreadable file or an invalid
    profile raises :class:`ProfileSelectionError`."""
    raw: dict[str, dict[str, Any]] = {k: dict(v) for k, v in _BUILTIN_PROFILES.items()}

    for layer in _layers(path):
        named = layer.get("profiles") or {}
        if not isinstance(named, dict):
            raise ProfileSelectionError('the providers file\'s "profiles" must be an object')
        for name, spec in named.items():
            if not isinstance(spec, dict):
                continue
            if not isinstance(spec.get("extends", ""), str):
                raise ProfileSelectionError(f'profile {name!r}: "extends" must name a profile')
            # "extends": start from another profile and change only what differs
            parent = raw.get(spec.get("extends", ""), {}) if "extends" in spec else {}
            own = {k: v for k, v in spec.items() if k != "extends"}
            if "api_key_env" in own and "api_key_literal" not in own:
                parent = {k: v for k, v in parent.items() if k != "api_key_literal"}
            raw[name] = {**raw.get(name, {}), **parent, **own, "name": name}

    e = env if env is not None else credentials.overlay()
    profiles: dict[str, ModelProfile] = {}
    for name, spec in raw.items():
        # Per-profile env overrides, NAME upper-cased with - as _:
        #   TR1_<NAME>_BASE_URL  point a profile at a different endpoint (remote/alt port)
        #   TR1_<NAME>_MODEL     serve a different model (e.g. TR1_OLLAMA_MODEL=llama3.2)
        #   TR1_<NAME>_API_KEY   the credential a self-served endpoint expects (a vLLM
        #                         started with an --api-key / VLLM_API_KEY); keyless stays
        #                         the default, so an unset var keeps the profile's literal
        env_key = name.upper().replace("-", "_")
        base_override = e.get(f"TR1_{env_key}_BASE_URL")
        model_override = e.get(f"TR1_{env_key}_MODEL")
        provider = LOCAL_READERS.get(name)
        key_override = e.get(f"TR1_{env_key}_API_KEY")
        if provider and spec == _BUILTIN_PROFILES.get(name):
            # the reader of a server you run is on that server, with its model and key, unless told
            if not base_override:  # a reader told to go elsewhere does not carry the server's key
                key_override = key_override or e.get(f"TR1_{provider}_API_KEY")
            base_override = base_override or e.get(f"TR1_{provider}_BASE_URL")
            if not e.get(f"TR1_{env_key}_BASE_URL"):
                model_override = model_override or e.get(f"TR1_{provider}_MODEL")
        if base_override:
            spec = {**spec, "base_url": base_override}
        if model_override:
            spec = {**spec, "model": model_override}
        if key_override:
            spec = {**spec, "api_key_literal": key_override}
        if not spec.get("base_url") and not spec.get("api_key_env"):
            spec = {**spec, "api_key_env": "OPENAI_API_KEY"}  # no address: it is OpenAI's own
        try:
            profiles[name] = ModelProfile(**spec)
        except ValueError as err:  # pydantic's validation error
            raise ProfileSelectionError(f"profile {name!r} is invalid: {err}") from err
    return profiles


NO_MODEL = (
    "No model provider set up. Get a Tauric key at https://tauric.ai/signup, or set "
    "OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY or TR1_VLLM_BASE_URL."
)


def _file_default(path: str | Path | None) -> str | None:
    """The ``"default"`` selection of the last providers file that sets one."""
    return next((d for layer in reversed(_layers(path)) if (d := layer.get("default"))), None)


def default_backbone_choice(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str] | tuple[None, None]:
    """The backbone a run starts on when the caller names none, and why: ``(name, reason)`` with
    reason ``env`` (``TR1_PROFILE``), ``chosen`` (the user's persisted ``/model`` choice, ignored
    if it no longer names a backbone), ``file`` (the providers file's ``"default"`` pin),
    ``supplied`` (the first provider the user has supplied: a hosted API's key, then a server they
    run), or ``(None, None)`` when nothing is set up. A typo'd ``TR1_PROFILE`` or file pin raises.
    Credential-aware but probe-free: a local server is "supplied" by the URL the user set, not by
    answering."""
    e = env if env is not None else credentials.overlay()
    rows = available_backbones(path=path, env=e)  # selectable BACKBONES (readers excluded)
    names = {r["name"] for r in rows}

    def _explicit(name: str | None, source: str) -> str | None:
        # an explicit selection must name a real backbone (not a reader), else it is surfaced
        if name and name not in names:
            raise ProfileSelectionError(f"{source} selects unknown backbone {name!r}")
        return name or None

    if env_sel := _explicit(e.get("TR1_PROFILE"), "TR1_PROFILE"):
        return env_sel, "env"
    persisted = persisted_backbone()  # sticky /model choice; fail-soft (ignored if not a backbone)
    if persisted and persisted in names:
        return persisted, "chosen"
    if file_sel := _explicit(_file_default(path), 'file "default"'):
        return file_sel, "file"
    usable = {r["name"] for r in rows if r["configured"]}
    if (provider := supplied_provider(e, usable)) is not None:
        return provider.name, "supplied"
    return None, None


def default_backbone_name(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """The auto-default backbone, if any (see :func:`default_backbone_choice`)."""
    return default_backbone_choice(path, env)[0]


def get_profile(
    name: str | None = None,
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ModelProfile:
    """Resolve a profile by name; with no name, resolve the auto-default
    (:func:`default_backbone_name`). An unknown *named* profile, a typo'd explicit selection,
    or nothing set up raises :class:`ProfileSelectionError` (HTTP callers convert it to a 4xx)."""
    profiles = load_profiles(path=path, env=env)
    resolved = name or default_backbone_name(path=path, env=env)
    if resolved is None:
        raise ProfileSelectionError(NO_MODEL)
    try:
        return profiles[resolved]
    except KeyError:
        raise ProfileSelectionError(f"unknown profile {resolved!r}") from None


def available_backbones(
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The selectable backbones (``role == "backbone"``) with presentation metadata and
    config-level credential status; page readers are excluded. Rows come in a stable
    display order (:attr:`DisplayMeta.order`) independent of readiness, so the picker never
    reshuffles between openings. ``configured`` means the backbone *can be built* (keyless
    local, or its API key resolves); live reachability (``ready``) is layered on by the
    ``/models`` endpoint, which probes the endpoint and its paired reader; being configured
    is not the same as being up."""
    e = env if env is not None else credentials.overlay()
    rows: list[dict[str, Any]] = []
    for p in load_profiles(path=path, env=e).values():
        if p.role != "backbone":  # readers are paired internally, not user-selectable
            continue
        d = p.display or DisplayMeta()
        keyless = p.api_key_literal is not None or p.api_key_env is None
        configured = keyless or bool(p.resolve_api_key(e))
        rows.append(
            {
                "name": p.name,
                "model": p.model,
                "kind": d.kind,
                "provider_label": d.provider_label or p.name,
                "order": d.order,
                "configured": configured,
                # the exact env var to set when a *cloud* backbone lacks its key; None for a
                # keyless local backbone (nothing to configure; it just needs to be running).
                "api_key_env": None if keyless else p.api_key_env,
            }
        )
    rows.sort(key=lambda m: (m["order"], m["name"]))  # stable; never reshuffles on readiness
    return rows
