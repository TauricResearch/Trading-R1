#!/bin/sh
# Install Trading-R1: uv and bun if missing, then the `trading-r1` command.
set -eu

die() { echo "Trading-R1 install failed: $1" >&2; exit 1; }
run_from() {
  script=$(curl -fsSL "$1") || die "could not download $1"
  printf '%s\n' "$script" | PATH=$shell_path "$2" || die "$1 failed"
}

case "$(uname -s)" in Linux | Darwin) ;; *) die "Linux or macOS is required" ;; esac
command -v curl > /dev/null || die "curl is required"

# The installers see the user's PATH, so that they add themselves to the shell profile.
shell_path=$PATH
PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:${BUN_INSTALL:-$HOME/.bun}/bin:$PATH"

command -v uv > /dev/null || run_from https://astral.sh/uv/install.sh sh
if ! command -v bun > /dev/null; then
  command -v unzip > /dev/null && command -v bash > /dev/null \
    || die "installing bun needs unzip and bash"
  run_from https://bun.sh/install bash
fi

uv tool install --upgrade trading-r1 || die "uv could not install trading-r1"
uv tool update-shell > /dev/null 2>&1 || true
"$(uv tool dir --bin)/trading-r1" --version > /dev/null || die "trading-r1 does not run"

if (PATH=$shell_path; command -v trading-r1 > /dev/null); then
  echo "Trading-R1 is installed. Run: trading-r1"
else
  echo "Trading-R1 is installed. Open a new shell, then run: trading-r1"
fi
