# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""`trading-r1`: launches the Trading-R1 terminal.

trading-r1                       # the TUI (backend + UI)
trading-r1 "NVDA DCF value?"     # the TUI, seeded with a first question
trading-r1 --profile vllm        # the TUI on a given backbone (/model switches later)
trading-r1 serve                 # the HTTP+SSE backend alone (development)
trading-r1 export SESSION        # write a session's report to a file
trading-r1 jobs | channels | secrets | daemon | sync | sessions   # subcommands, in cli/*.py

``main`` is the console-script entry point. Exit status is 0 on success and non-zero when a
subcommand fails; each subcommand is a module with a ``main`` that returns its own status.
``terminal`` runs the TUI, and ``client`` is the subcommands' shared call to the running backend.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from trading_r1 import __version__
from trading_r1.providers import ProfileSelectionError, available_backbones
from trading_r1.runtime.modes import MODES

# The subcommands: name to module, resolved when one is run.
_SUBCOMMANDS = {
    "serve": "trading_r1.cli.backend",
    "export": "trading_r1.cli.export",
    "jobs": "trading_r1.cli.jobs",
    "channels": "trading_r1.cli.channels",
    "secrets": "trading_r1.cli.credentials",
    "daemon": "trading_r1.cli.daemon",
    "sync": "trading_r1.cli.sync",
    "sessions": "trading_r1.cli.sessions",
}

_FILES = """\
files, under ~/.trading-r1 (TR1_HOME moves it):
  settings.json     what Settings saves
  credentials.json  stored keys
  providers.json    add or change a model profile, for example
                    {"profiles": {"my-qwen": {"extends": "vllm",
                     "base_url": "http://gpu-box:9000/v1", "reader_profile": "my-qwen"}}}
                    A new profile with its own address sends no key unless it names one
                    ("api_key_env": "MY_KEY"). One that extends a hosted profile keeps
                    that profile's key, so name the key it should send.
  runtime.json      limits: timeouts, retries, concurrency, token budgets, compaction

environment:
  TR1_PROFILE, TR1_REPORTS_DIR, TR1_PORT, TR1_LOG_LEVEL, TR1_LOG_FILE, TR1_SKILLS_DIR
  TR1_<NAME>_BASE_URL, TR1_<NAME>_MODEL for a model, for example TR1_VLLM_BASE_URL
  TR1_VLLM_READER_BASE_URL, the page reader, if on another server

Without --profile or a saved choice, the model is the first set up: an OpenAI, Anthropic, Gemini or
Tauric key, then a vLLM server (TR1_VLLM_BASE_URL) or an Ollama (TR1_OLLAMA_MODEL or
TR1_OLLAMA_BASE_URL)."""


def main() -> None:
    """Entry point: the TUI by default; a subcommand's name runs that module's ``main``."""
    if sys.argv[1:2] and sys.argv[1] in _SUBCOMMANDS:
        module = importlib.import_module(_SUBCOMMANDS[sys.argv[1]])
        raise SystemExit(module.main(sys.argv[2:]))

    ap = argparse.ArgumentParser(
        prog="trading-r1",
        description="Trading-R1 terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="also: trading-r1 " + " | ".join(_SUBCOMMANDS) + " (each takes --help)\n\n" + _FILES,
    )
    ap.add_argument("--version", action="version", version=f"trading-r1 {__version__}")
    ap.add_argument("question", nargs="?", help="a first question to ask")
    ap.add_argument(
        "--mode",
        choices=list(MODES),
        default=None,
        help="the starting execution mode (default: auto). auto = web + tools; "
        "analytical = numbers-first.",
    )
    ap.add_argument(
        "--profile",
        default=None,
        help="the model to start on (tauric, gpt, anthropic, gemini, vllm, ollama); "
        "default: the saved Model… choice, else the first with its key set.",
    )
    args = ap.parse_args()
    try:
        backbones = [b["name"] for b in available_backbones()]
    except ProfileSelectionError as err:
        ap.error(str(err))
    if args.profile and args.profile not in backbones:
        ap.error(f"unknown --profile {args.profile!r}; backbones: {', '.join(backbones)}")
    if (named := os.environ.get("TR1_PROFILE")) and named not in backbones:
        ap.error(f"unknown TR1_PROFILE {named!r}; backbones: {', '.join(backbones)}")
    from trading_r1.cli import terminal

    raise SystemExit(
        terminal._tui(initial_prompt=args.question, profile=args.profile, mode=args.mode)
    )


if __name__ == "__main__":
    main()
