"""The garage's user service: the systemd unit, the launchd agent, and the commands that drive them.

Text and argv lists only. Nothing here runs `systemctl`, `launchctl` or
`loginctl`; the CLI (Task 15) runs what these return.

The 5-minute stop timeout is deliberate: on its way down the service ends
jobs and runs every runner's cleanup and stop, and the service managers'
defaults (90 seconds for systemd, about 20 for launchd) would cut that short.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import garage_paths

SERVICE = "pitcall-garage"
LAUNCHD_LABEL = "dev.pitcall.garage"
#: Split over two source lines only; the plist carries it as one.
PLIST_DOCTYPE = ('<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                 '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">')


def _systemd_word(value: str) -> str:
    """One unit-file word: `%` doubled, quoted when it holds a space, quote or backslash."""
    value = value.replace("%", "%%")
    if any(ch in value for ch in ' \t"\\'):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _systemd_arg(value: str) -> str:
    """One ExecStart word: `$` doubled too, since ExecStart expands variables (Environment= does
    not)."""
    return _systemd_word(value.replace("$", "$$"))


def systemd_unit(python: str, entry: Path, path: str | None = None) -> str:
    """`path`: the PATH the service runs with, so the recipes find `docker` or `lxc` where the
    shell that ran `garage start` does."""
    environment = f"Environment={_systemd_word('PATH=' + path)}\n" if path else ""
    return (
        "[Unit]\n"
        "Description=pitcall garage\n"
        "\n"
        "[Service]\n"
        f"ExecStart={_systemd_arg(python)} {_systemd_arg(str(entry))}\n"
        f"{environment}"
        "Restart=on-failure\n"
        "TimeoutStopSec=5min\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist(python: str, entry: Path, log_path: Path, path: str | None = None) -> str:
    """`path`: the PATH the agent runs with; launchd's own, `/usr/bin:/bin:/usr/sbin:/sbin`, has
    neither Docker Desktop's nor Homebrew's `docker`."""
    def text(value):
        return escape(str(value))
    environment = ("  <key>EnvironmentVariables</key>\n"
                   "  <dict>\n"
                   "    <key>PATH</key>\n"
                   f"    <string>{text(path)}</string>\n"
                   "  </dict>\n") if path else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
{PLIST_DOCTYPE}
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{text(python)}</string>
    <string>{text(entry)}</string>
  </array>
{environment}  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ExitTimeOut</key>
  <integer>300</integer>
  <key>StandardOutPath</key>
  <string>{text(log_path)}</string>
  <key>StandardErrorPath</key>
  <string>{text(log_path)}</string>
</dict>
</plist>
"""


def systemd_unit_path(env=None) -> Path:
    return garage_paths.config_home(env) / "systemd" / "user" / f"{SERVICE}.service"


def launchd_plist_path(env=None) -> Path:
    return garage_paths.expand("~", env) / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _unsupported(platform: str):
    return ValueError(f"the garage runs on Linux (systemd) and macOS (launchd), not {platform}")


def start_commands(platform: str, uid: int, running: bool, env=None) -> list[list[str]]:
    if platform == "linux":
        commands = [["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", SERVICE]]
        if running:
            commands.append(["systemctl", "--user", "restart", SERVICE])
        return commands
    if platform == "darwin":
        commands = [["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"]] if running else []
        # A service `stop` disabled cannot be bootstrapped until it is enabled again.
        return commands + [["launchctl", "enable", f"gui/{uid}/{LAUNCHD_LABEL}"],
                           ["launchctl", "bootstrap", f"gui/{uid}", str(launchd_plist_path(env))]]
    raise _unsupported(platform)


def stop_commands(platform: str, uid: int, running: bool = True) -> list[list[str]]:
    if platform == "linux":
        return [["systemctl", "--user", "disable", "--now", SERVICE]]
    if platform == "darwin":
        # bootout alone lasts until the next login, which loads every LaunchAgents plist again;
        # and launchctl refuses to boot out an agent it has not loaded.
        disable = [["launchctl", "disable", f"gui/{uid}/{LAUNCHD_LABEL}"]]
        return disable + ([["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"]]
                          if running else [])
    raise _unsupported(platform)


def status_command(platform: str, uid: int) -> list[str]:
    if platform == "linux":
        return ["systemctl", "--user", "is-active", SERVICE]
    if platform == "darwin":
        return ["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"]
    raise _unsupported(platform)


def linger_enabled(show_user_output: str) -> bool:
    """`loginctl show-user <user> -p Linger` says `Linger=yes` when the service outlives logout."""
    return any(line.strip() == "Linger=yes" for line in show_user_output.splitlines())
