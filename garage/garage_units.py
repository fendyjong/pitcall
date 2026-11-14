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


def _systemd_arg(value: str) -> str:
    """One ExecStart word: `%` and `$` doubled, quoted when it holds a space, quote or backslash."""
    value = value.replace("%", "%%").replace("$", "$$")
    if any(ch in value for ch in ' \t"\\'):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def systemd_unit(python: str, entry: Path) -> str:
    return (
        "[Unit]\n"
        "Description=pitcall garage\n"
        "\n"
        "[Service]\n"
        f"ExecStart={_systemd_arg(python)} {_systemd_arg(str(entry))}\n"
        "Restart=on-failure\n"
        "TimeoutStopSec=5min\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist(python: str, entry: Path, log_path: Path) -> str:
    def text(value):
        return escape(str(value))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{text(python)}</string>
    <string>{text(entry)}</string>
  </array>
  <key>RunAtLoad</key>
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


def stop_commands(platform: str, uid: int) -> list[list[str]]:
    if platform == "linux":
        return [["systemctl", "--user", "disable", "--now", SERVICE]]
    if platform == "darwin":
        # bootout alone lasts until the next login, which loads every LaunchAgents plist again.
        return [["launchctl", "disable", f"gui/{uid}/{LAUNCHD_LABEL}"],
                ["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"]]
    raise _unsupported(platform)


def status_command(platform: str, uid: int) -> list[str]:
    if platform == "linux":
        return ["systemctl", "--user", "is-active", SERVICE]
    if platform == "darwin":
        return ["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"]
    raise _unsupported(platform)


def linger_enabled(show_user_output: str) -> bool:
    """`loginctl show-user <user> -p Linger` prints `Linger=yes` when the service outlives logout."""
    return any(line.strip() == "Linger=yes" for line in show_user_output.splitlines())
