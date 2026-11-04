# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The backend as a background service, under the user's own service manager (a systemd user
unit on Linux, a LaunchAgent on macOS).

The unit runs ``trading-r1 serve``: the same backend the terminal would start, held by the service
manager instead of a terminal, so a scheduled job or a chat can reach it while no terminal is open.
A service manager does not read the shell profile, so ``install`` copies what is set now and names
it: keys into the credentials store, where the terminal's *API keys…* can change them, and settings
into ``daemon.env`` (owner-only).
"""

from __future__ import annotations

import contextlib
import getpass
import os
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

from trading_r1.host.instance import default_port, read_published, verify
from trading_r1.providers.builtins import model_key_names
from trading_r1.state import credentials
from trading_r1.state.fileio import atomic_write_text, file_lock
from trading_r1.state.paths import private_state_dir, state_dir

LABEL = "trading-r1"
ENV_FILE = "daemon.env"
MANAGED_ENV = "TR1_SERVICE"  # set by the unit: this backend is the service, not a terminal's
LOG_FILE = "daemon.log"
# What a background process needs from this shell: the API keys, and the TR1_* settings that
# point at servers, models and state. Never the per-process token or the auth switch.
_COPIED = re.compile(r"^TR1_(?!API_TOKEN$|API_AUTH$|API_URL$|SERVICE$)\w+$")
_SECRET = re.compile(r"(TOKEN|PASSWORD|SECRET|KEY)$")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def copied_environment(env: Mapping[str, str]) -> dict[str, str]:
    """The variables of ``env`` the daemon is given, in name order."""
    keys = model_key_names()
    return {k: env[k] for k in sorted(env) if (k in keys or _COPIED.match(k)) and env[k]}


def _is_key(name: str) -> bool:
    return name in model_key_names() or _SECRET.search(name) is not None


def _quote(value: str) -> str:
    """A value for a systemd ``EnvironmentFile``: double-quoted, with ``\\`` and ``"`` escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unit_quote(value: str) -> str:
    """A value on a unit-file line: quoted, and ``%`` doubled so no specifier is expanded."""
    return _quote(value).replace("%", "%%")


class Manager(ABC):
    """One platform's service manager: the file it reads and the commands that drive it."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or Path.home()

    @property
    @abstractmethod
    def unit_path(self) -> Path: ...

    @abstractmethod
    def render(self, exe: str, port: int, env: Mapping[str, str]) -> str:
        """The unit file."""

    @abstractmethod
    def commands(self, action: str) -> list[list[str]]:
        """The commands that perform ``action`` (install, uninstall, start, stop, restart,
        status, logs)."""

    @property
    def writes_env_file(self) -> bool:
        """Whether the settings go to ``daemon.env`` (the unit reads it) or into the unit."""
        return True

    def keep_running(self) -> str:
        """Ask that the service outlive a logout; what the user should know about it."""
        return ""

    def keeps_running(self) -> bool | None:
        """Whether the service outlives a logout; None when that cannot be told."""
        return None


class SystemdUser(Manager):
    unit = f"{LABEL}.service"

    @property
    def unit_path(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / self.unit

    def render(self, exe: str, port: int, env: Mapping[str, str]) -> str:
        env_file = state_dir() / ENV_FILE
        if re.search(r"[\s%\"\\]", str(env_file)):
            raise ValueError(
                f"the state directory {state_dir()} has a character a unit cannot hold"
            )
        lines = [
            "[Unit]",
            "Description=Trading-R1 backend",
            "After=network-online.target",
            "StartLimitIntervalSec=60",
            "StartLimitBurst=5",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={_exec(exe)} serve --port {port} --wait",
            "Restart=always",
            "RestartSec=5",
            "SuccessExitStatus=143",
            "TimeoutStopSec=20",
            f"EnvironmentFile=-{env_file}",
            f"Environment={_unit_quote('PATH=' + os.environ.get('PATH', '/usr/bin:/bin'))}",
            f"Environment={_unit_quote('TR1_HOME=' + str(state_dir()))}",
            f"Environment={MANAGED_ENV}=1",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
        return "\n".join(lines)

    def commands(self, action: str) -> list[list[str]]:
        ctl = ["systemctl", "--user"]
        return {
            # restart, not just start: a unit that is already running takes the new settings
            "install": [
                ctl + ["daemon-reload"],
                ctl + ["enable", self.unit],
                ctl + ["restart", self.unit],
            ],
            "uninstall": [ctl + ["disable", "--now", self.unit]],
            "reload": [ctl + ["daemon-reload"]],
            "start": [ctl + ["start", self.unit]],
            "stop": [ctl + ["stop", self.unit]],
            "restart": [ctl + ["restart", self.unit]],
            "status": [ctl + ["is-active", self.unit]],
            "logs": [["journalctl", "--user", "-u", self.unit, "-n", "100", "--no-pager"]],
        }[action]

    def keep_running(self) -> str:
        if _succeeds(["loginctl", "enable-linger", _user()]):
            return "it starts at boot and keeps running after you log out (lingering is on)"
        return (
            "lingering could not be turned on, so it stops when you log out: "
            "loginctl enable-linger $USER"
        )

    def keeps_running(self) -> bool | None:
        try:
            done = _run(["loginctl", "show-user", _user(), "-p", "Linger", "--value"])
        except OSError:
            return None
        return done.stdout.strip() == "yes" if done.returncode == 0 else None


def _succeeds(cmd: list[str]) -> bool:
    try:
        return _run(cmd).returncode == 0
    except OSError:  # the program is not here
        return False


def _user() -> str:
    return getpass.getuser()


def _exec(exe: str) -> str:
    """The program path as systemd's ``ExecStart`` wants it: quoted, ``%`` doubled."""
    return _unit_quote(exe)


class Launchd(Manager):
    label = f"ai.tauric.{LABEL}"

    @property
    def unit_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{self.label}.plist"

    @property
    def writes_env_file(self) -> bool:
        return False  # launchd reads no environment file: the variables go into the plist

    def render(self, exe: str, port: int, env: Mapping[str, str]) -> str:
        from xml.sax.saxutils import escape

        variables = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}
        variables["TR1_HOME"] = str(state_dir())
        variables[MANAGED_ENV] = "1"
        pairs = "".join(
            f"<key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in variables.items()
        )
        log = escape(str(state_dir() / LOG_FILE))
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>'
            f"<key>Label</key><string>{self.label}</string>"
            f"<key>ProgramArguments</key><array><string>{escape(exe)}</string>"
            f"<string>serve</string><string>--port</string><string>{port}</string>"
            "<string>--wait</string></array>"
            f"<key>EnvironmentVariables</key><dict>{pairs}</dict>"
            "<key>RunAtLoad</key><true/>"
            "<key>KeepAlive</key><true/>"
            "<key>ThrottleInterval</key><integer>10</integer>"
            f"<key>StandardOutPath</key><string>{log}</string>"
            f"<key>StandardErrorPath</key><string>{log}</string>"
            "</dict></plist>\n"
        )

    def keep_running(self) -> str:
        return "it runs while you are logged in and starts again at your next login"

    def commands(self, action: str) -> list[list[str]]:
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{self.label}"
        return {
            # a job that is loaded is replaced: unload (it may not be), then load
            "install": [
                ["launchctl", "bootout", target],
                ["launchctl", "bootstrap", domain, str(self.unit_path)],
            ],
            "uninstall": [["launchctl", "bootout", target]],
            "reload": [],
            # stop unloads: a SIGTERM would only have launchd restart it (KeepAlive)
            "start": [["launchctl", "bootstrap", domain, str(self.unit_path)]],
            "stop": [["launchctl", "bootout", target]],
            "restart": [
                ["launchctl", "bootout", target],
                ["launchctl", "bootstrap", domain, str(self.unit_path)],
            ],
            "status": [["launchctl", "print", target]],
            "logs": [["tail", "-n", "100", str(state_dir() / LOG_FILE)]],
        }[action]


def _find_user_bus() -> None:
    """``systemctl --user`` finds the user manager through ``XDG_RUNTIME_DIR``, which some shells
    do not set while the manager runs."""
    if not os.environ.get("XDG_RUNTIME_DIR"):
        runtime = Path(f"/run/user/{os.getuid()}")
        if (runtime / "bus").exists():
            os.environ["XDG_RUNTIME_DIR"] = str(runtime)


def manager_for(platform: str | None = None, home: Path | None = None) -> Manager:
    platform = platform or sys.platform
    if platform.startswith("linux"):
        _find_user_bus()
        return SystemdUser(home)
    if platform == "darwin":
        return Launchd(home)
    raise ServiceError("the daemon is supported on Linux (systemd) and macOS (launchd)")


def _write_private(path: Path, text: str) -> None:
    atomic_write_text(path, text)
    path.chmod(0o600)


def install(
    manager: Manager,
    exe: str,
    port: int,
    env: Mapping[str, str],
    *,
    copy_env: bool = True,
    run: Runner = _run,
) -> list[str]:
    """Write the unit (and ``daemon.env``), then enable and start it. Keys go to the credentials
    store, settings to the unit's environment. Returns the names of the variables copied."""
    copied = copied_environment(env) if copy_env else {}
    settings = {k: v for k, v in copied.items() if not _is_key(k)}
    state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    keys = {k: v for k, v in copied.items() if k not in settings}
    try:
        credentials.set_values(keys)
    except credentials.CredentialsError as err:
        raise RuntimeError(f"the keys could not be stored: {err}") from err
    if manager.writes_env_file:
        _write_private(
            state_dir() / ENV_FILE, "".join(f"{k}={_quote(v)}\n" for k, v in settings.items())
        )
    manager.unit_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(manager.unit_path, manager.render(exe, port, settings))
    _run_steps(manager.commands("install"), run)
    return list(copied)


def keys_replaced(env: Mapping[str, str]) -> list[str]:
    """The keys an install would change in the credentials store. The environment wins, as it
    does for anything started from this shell."""
    try:
        stored = credentials.read_store()
    except credentials.CredentialsError:
        return []
    copied = copied_environment(env)
    return [k for k, v in copied.items() if _is_key(k) and stored.get(k) not in (None, v)]


def _run_steps(commands: list[list[str]], run: Runner) -> None:
    """Run the commands in order. Only the last must succeed: the steps before it clear the way
    (unloading a job that is not loaded fails, and is fine)."""
    for i, cmd in enumerate(commands):
        done = run(cmd)
        if done.returncode != 0 and i == len(commands) - 1:
            raise RuntimeError(f"{' '.join(cmd)} failed: {(done.stderr or done.stdout).strip()}")


def uninstall(manager: Manager, *, run: Runner = _run) -> None:
    """Stop and disable the unit, and remove it and its settings and log. Keys stay in the
    credentials store, which is the user's, and lingering, which other services may use."""
    for cmd in manager.commands("uninstall"):
        run(cmd)  # not running or not loaded is fine
    manager.unit_path.unlink(missing_ok=True)
    (state_dir() / ENV_FILE).unlink(missing_ok=True)
    (state_dir() / LOG_FILE).unlink(missing_ok=True)
    for cmd in manager.commands("reload"):
        run(cmd)


def managed() -> bool:
    """Whether this process was started by the service manager (and so is the service)."""
    return os.environ.get(MANAGED_ENV) == "1"


class ServiceError(Exception):
    """A service change that cannot be made now; the message says why."""


@contextlib.contextmanager
def management_lock() -> Iterator[None]:
    """One install or removal at a time, whether it comes from the command line or the backend."""
    with file_lock(private_state_dir() / "service.lock"):
        yield


def status() -> dict[str, object]:
    """What the screen shows: whether a service manager is here, whether the unit is installed
    and loaded, whether this backend is the service, and which variables an install would copy."""
    try:
        manager = manager_for()
    except ServiceError:
        return {"supported": False}
    installed = manager.unit_path.exists()
    active = installed and _run(manager.commands("status")[0]).returncode == 0
    return {
        "supported": True,
        "installed": installed,
        "active": active,
        "managed": managed(),
        "copies": list(copied_environment(os.environ)),
        "keeps_running": manager.keeps_running() if installed else None,
    }


def kept_running() -> bool:
    """Whether scheduled runs outlast this backend: it is the service, or a service is running
    and takes over when it ends (one the user stopped stays stopped)."""
    if managed():
        return True
    try:
        manager = manager_for()
    except ServiceError:
        return False
    return manager.unit_path.exists() and _succeeds(manager.commands("status")[0])


def install_here() -> tuple[list[str], str]:
    """Install for the backend that asks: only when nothing is installed (an install restarts the
    service, which must not happen while it serves the request). Returns the copied names and what
    the user should know about how it keeps running."""
    exe = shutil.which("trading-r1") or str(Path(sys.argv[0]).resolve())
    with management_lock():
        manager = manager_for()
        if manager.unit_path.exists():
            raise ServiceError("the background service is already installed")
        try:
            copied = install(manager, exe, default_port(), os.environ, run=_run)
            return copied, manager.keep_running()
        except RuntimeError:  # the manager refused: leave no half-installed service behind
            manager.unit_path.unlink(missing_ok=True)
            (state_dir() / ENV_FILE).unlink(missing_ok=True)
            raise


def remove_here() -> None:
    """Remove the service, except from the service itself: stopping it would end this request
    before the files are gone."""
    if managed():
        raise ServiceError(
            "this backend is the background service; remove it from a shell: "
            "trading-r1 daemon uninstall"
        )
    with management_lock():
        uninstall(manager_for(), run=_run)


def unit_state(manager: Manager) -> str:
    """The first line the service manager says about the unit."""
    done = _run(manager.commands("status")[0])
    text = (done.stdout or done.stderr).strip()
    return text.splitlines()[0] if text else "unknown"


def backend_report() -> str:
    """What the published backend says about itself, or why nothing does."""
    backend = read_published()
    if backend is None:
        return "no backend is published"
    if not verify(backend):
        return f"a backend is published at {backend.url} but does not answer"
    return f"serving at {backend.url} (pid {backend.pid}, version {backend.version})"
