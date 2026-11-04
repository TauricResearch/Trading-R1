# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Configuration: one typed place for what a user or operator may tune (generation budgets, run
limits, recovery, the page reader, probes, fetching, market data, jobs, the service, delivery).
A value that is a fact about something else (an API's limit, a protocol's framing) stays named
beside what it describes.

Precedence (low to high): built-in defaults <
this machine's file (``~/.trading-r1/runtime.json``, ``$TR1_RUNTIME_CONFIG``) < explicit
``overrides`` (test seam) < environment variables. Budgets depend on the serving hardware, so a
machine sets its own in its file rather than in code. The loader never raises on a
missing/malformed file: the harness always has a usable config.
Resource limits are anti-runaway *backstops* sized generous; the bounded round
count is what guarantees termination.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_r1.state.paths import state_dir


class _Section(BaseModel):
    # Tolerate unknown keys in the file rather than crashing the loader.
    model_config = ConfigDict(extra="ignore")


class GenerationConfig(_Section):
    """Main model generation token budget and truncation escalation settings."""

    max_tokens: int = 16384
    max_tokens_ceiling: int = 65536
    max_tokens_escalation_factor: int = 2


class LimitsConfig(_Section):
    """Run, call, round, and retry caps that prevent runaway execution. A single model call is
    bounded by ``per_call_timeout_s`` and by the agent's wrap-up time (``run_timeout_s`` less
    ``wrap_up_reserve_s``), and ``run_timeout_s`` is a hard ceiling on everything. Set per
    machine (its serving speed) in its own file."""

    per_call_timeout_s: float = 600  # 10 min: one model call should never exceed this
    run_timeout_s: int = 2700  # 45 min: hard ceiling on the whole run
    max_rounds: int = 100
    max_transient_retries: int = 10
    max_truncation_escalations: int = 3
    max_regenerate_retries: int = 3
    # An agent stops researching this long before its timeout and writes its answer; no model
    # call runs into it. Size it to one answer-writing call on the serving hardware. An agent keeps
    # at most half its budget for this.
    wrap_up_reserve_s: int = Field(240, ge=0)


class BackoffConfig(_Section):
    """Transient-error backoff parameters."""

    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter: bool = True


class RecoveryConfig(_Section):
    """Context-window and compaction controls used by model-call recovery. With ``auto_compact``
    on, a run cuts its old tool outputs to pointers, and a session summarizes its history, once
    the context passes ``compaction_threshold`` of the model's window; off, a run that fills the
    window stops and says so (``/compact`` stays the user's call). ``context_window`` is the
    window assumed for a model that states none."""

    auto_compact: bool = True
    compaction_threshold: float = 0.8
    compaction_keep_recent_tool_results: int = 4
    context_window: int = 262144


class ReaderConfig(_Section):
    """Generation budget for the page-reader extraction call, separate from the
    agent's main budget so it's tunable independently."""

    max_tokens: int = 8192  # generous so reasoning-model readers leave room for output
    # How many pages `visit` fetches+reads concurrently, bounded so a run doesn't hammer the
    # reader endpoint or trip rate limits. A local batching server can take more; a
    # rate-limited cloud API less.
    max_concurrency: int = 5
    char_budget: int = 24000  # how much of a page goes to the reader call


class VerifyConfig(_Section):
    """The verification stage: after the report is written, the agent checks its numbers against
    the record with its tools and rewrites what differs. Off by default (it costs a few more
    rounds); the user turns it on in the settings."""

    enabled: bool = False
    max_rounds: int = 4  # tool rounds the check may use before it must write the report


class GroundingConfig(_Section):
    """The grounding review: after a report is written, the model rereads it against what it read
    and attributes or softens what it cannot tie to a source. Off by default; a setting."""

    enabled: bool = False


class ProbeConfig(_Section):
    """The check of a local model server before a run. A busy server can miss one probe and be
    fine, so a run does not start or fail on a single miss."""

    timeout_s: float = 3.0
    attempts: int = Field(3, ge=1)
    pause_s: float = 2.0


class FetchConfig(_Section):
    """Reading the web: one page, and the queries of a search."""

    timeout_s: float = 30.0  # the whole download of a page
    quick_timeout_s: float = 8.0  # checking that a search result still answers
    attempts: int = Field(3, ge=1)
    backoff_s: float = 1.5
    query_concurrency: int = Field(5, ge=1)
    validate_concurrency: int = Field(8, ge=1)


class DataConfig(_Section):
    """Calls to the market-data API."""

    connect_timeout_s: float = 10.0
    read_timeout_s: float = 60.0
    attempts: int = Field(4, ge=1)


class JobsConfig(_Section):
    """Scheduled reports and chat turns. A run is stopped at ``run_tokens``, and is charged that
    against ``daily_tokens`` (a rolling 24 hours) until it reports its real usage."""

    run_tokens: int = 1_500_000
    daily_tokens: int = 6_000_000
    concurrency: int = Field(2, ge=1)
    late_grace_s: float = 1800.0  # a fire missed by more than this is not run late
    min_interval_s: float = 900.0  # the closest two fires of a schedule may be
    sessions_kept: int = Field(30, ge=1)  # per job: the logs of its newest runs


class ServiceConfig(_Section):
    """The local service."""

    max_research_runs: int = Field(3, ge=1)  # interactive runs at once
    # how long a run's `ask_user` waits for a reply before it takes the recommended option
    ask_user_timeout_s: float = 300.0


class DeliveryConfig(_Section):
    """Sending reports through channels."""

    send_timeout_s: float = 30.0
    max_attachment_bytes: int = 5_000_000


class RuntimeConfig(_Section):
    """All configuration sections after defaults/file/env merging."""

    generation: GenerationConfig = GenerationConfig()
    limits: LimitsConfig = LimitsConfig()
    backoff: BackoffConfig = BackoffConfig()
    recovery: RecoveryConfig = RecoveryConfig()
    reader: ReaderConfig = ReaderConfig()
    verify: VerifyConfig = VerifyConfig()
    grounding: GroundingConfig = GroundingConfig()
    probe: ProbeConfig = ProbeConfig()
    fetch: FetchConfig = FetchConfig()
    data: DataConfig = DataConfig()
    jobs: JobsConfig = JobsConfig()
    service: ServiceConfig = ServiceConfig()
    delivery: DeliveryConfig = DeliveryConfig()


# env var -> (section, key, caster). Highest precedence so a live deployment is
# tunable without editing the file.
_log = logging.getLogger(__name__)

_ENV_OVERRIDES: dict[str, tuple[str, str, Callable[[str], Any]]] = {
    "TR1_MAX_TOKENS": ("generation", "max_tokens", int),
    "TR1_PER_CALL_TIMEOUT_S": ("limits", "per_call_timeout_s", int),
    "TR1_RUN_TIMEOUT_S": ("limits", "run_timeout_s", int),
    "TR1_MAX_LLM_CALL_PER_RUN": ("limits", "max_rounds", int),
    "TR1_WRAP_UP_RESERVE_S": ("limits", "wrap_up_reserve_s", int),
    "TR1_JOBS_RUN_TOKENS": ("jobs", "run_tokens", int),
    "TR1_JOBS_DAILY_TOKENS": ("jobs", "daily_tokens", int),
    "TR1_CONTEXT_WINDOW": ("recovery", "context_window", int),
    "TR1_VERIFY": ("verify", "enabled", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
    "TR1_GROUNDING": (
        "grounding",
        "enabled",
        lambda v: v.strip().lower() in ("1", "true", "yes", "on"),
    ),
}


def _strip_comments(value: Any) -> Any:
    """Drop ``_``-prefixed keys recursively so the file can self-document."""
    if isinstance(value, dict):
        return {
            k: _strip_comments(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    return value


def _deep_merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``over`` onto a copy of ``base`` (dicts merge; scalars
    replace)."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _unknown_keys(
    given: Mapping[str, Any], known: Mapping[str, Any], prefix: str = ""
) -> list[str]:
    """Dotted paths in `given` that the config has no key for (a typo or a retired setting)."""
    out: list[str] = []
    for k, v in given.items():
        if k not in known:
            out.append(f"{prefix}{k}")
        elif isinstance(v, Mapping) and isinstance(known[k], Mapping):
            out += _unknown_keys(v, known[k], f"{prefix}{k}.")
    return out


def default_machine_config_file() -> Path:
    """This machine's runtime config: ``TR1_RUNTIME_CONFIG`` if set, else
    ``~/.trading-r1/runtime.json`` (beside providers.json)."""
    env = os.environ.get("TR1_RUNTIME_CONFIG")
    return Path(env).expanduser() if env else state_dir() / "runtime.json"


def load_runtime_config(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> RuntimeConfig:
    """Load the runtime config applying defaults < machine file < overrides < env."""
    # A caller pinning ``env`` (tests) opts out of this machine's runtime.json, keeping the load
    # hermetic; a real run (``env is None``) picks it up.
    real_env = env is None
    env = os.environ if env is None else env
    merged = RuntimeConfig().model_dump()

    files = [Path(path)] if path is not None else []
    if real_env:
        files.append(default_machine_config_file())
    for p in files:
        if not p.exists():
            continue
        try:
            file_cfg = _strip_comments(json.loads(p.read_text()))
            if isinstance(file_cfg, dict):
                if unknown := _unknown_keys(file_cfg, merged):
                    _log.warning("config: %s has keys nothing reads: %s", p, ", ".join(unknown))
                candidate = _deep_merge(merged, file_cfg)
                RuntimeConfig(**candidate)  # a hand-edited file with a bad value is skipped whole
                merged = candidate
        except (json.JSONDecodeError, OSError, ValueError):  # ValidationError is a ValueError
            _log.warning("config: ignoring unreadable or invalid runtime config at %s", p)

    if overrides:
        merged = _deep_merge(merged, _strip_comments(overrides))

    for env_name, (section, key, cast) in _ENV_OVERRIDES.items():
        raw = env.get(env_name)
        if not raw:
            continue
        # ignore an unparseable env value; keep the lower layer
        with contextlib.suppress(ValueError, TypeError):
            merged[section][key] = cast(raw)

    return RuntimeConfig(**merged)


_settled: tuple[tuple[Any, ...], RuntimeConfig] | None = None


def runtime_config() -> RuntimeConfig:
    """The configuration in effect, read again when this machine's file or the environment
    changes."""
    global _settled
    path = default_machine_config_file()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = None
    key = (path, stamp, *(os.environ.get(name) for name in _ENV_OVERRIDES))
    if _settled is None or _settled[0] != key:
        _settled = (key, load_runtime_config())
    return _settled[1]
