"""The garage's systemd unit and launchd agent, as text (Task 6). Nothing here runs a service manager."""

import configparser
import plistlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
import garage_units  # noqa: E402

ENTRY = Path("/home/op/.local/share/pitcall/garage/garage_service.py")
HOME = {"HOME": "/home/op"}


def test_the_systemd_unit_restarts_on_failure_and_waits_five_minutes_to_stop():
    unit = configparser.ConfigParser(interpolation=None)
    unit.optionxform = str
    unit.read_string(garage_units.systemd_unit("/usr/bin/python3", ENTRY))
    assert unit["Service"]["ExecStart"] == f"/usr/bin/python3 {ENTRY}"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["TimeoutStopSec"] == "5min"
    assert unit["Install"]["WantedBy"] == "default.target"
    assert unit["Unit"]["Description"] == "pitcall garage"


def test_an_execstart_path_with_a_space_or_percent_is_quoted_for_systemd():
    unit = garage_units.systemd_unit("/opt/my python/bin/python3", Path("/home/op/100%/svc.py"))
    assert 'ExecStart="/opt/my python/bin/python3" /home/op/100%%/svc.py\n' in unit


def test_the_launchd_plist_keeps_the_service_alive_and_waits_300_seconds():
    log = Path("/Users/op/.local/state/pitcall/garage.log")
    plist = plistlib.loads(garage_units.launchd_plist("/usr/bin/python3", ENTRY, log).encode())
    assert plist == {
        "Label": "dev.pitcall.garage",
        "ProgramArguments": ["/usr/bin/python3", str(ENTRY)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ExitTimeOut": 300,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }


def test_plist_text_is_xml_escaped():
    plist = plistlib.loads(garage_units.launchd_plist("/a&b/python3", Path("/x<y>.py"),
                                                      Path("/l.log")).encode())
    assert plist["ProgramArguments"] == ["/a&b/python3", "/x<y>.py"]


def test_unit_paths():
    assert garage_units.systemd_unit_path(HOME) == Path(
        "/home/op/.config/systemd/user/pitcall-garage.service")
    assert garage_units.systemd_unit_path({**HOME, "XDG_CONFIG_HOME": "/x/c"}) == Path(
        "/x/c/systemd/user/pitcall-garage.service")
    assert garage_units.launchd_plist_path(HOME) == Path(
        "/home/op/Library/LaunchAgents/dev.pitcall.garage.plist")


def test_linux_commands():
    assert garage_units.start_commands("linux", 1000, running=False) == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "pitcall-garage"]]
    assert garage_units.start_commands("linux", 1000, running=True)[-1] == [
        "systemctl", "--user", "restart", "pitcall-garage"]
    assert garage_units.stop_commands("linux", 1000) == [
        ["systemctl", "--user", "disable", "--now", "pitcall-garage"]]
    assert garage_units.status_command("linux", 1000) == [
        "systemctl", "--user", "is-active", "pitcall-garage"]


def test_macos_commands():
    plist = str(garage_units.launchd_plist_path())
    assert garage_units.start_commands("darwin", 501, running=False) == [
        ["launchctl", "bootstrap", "gui/501", plist]]
    assert garage_units.start_commands("darwin", 501, running=True) == [
        ["launchctl", "bootout", "gui/501/dev.pitcall.garage"],
        ["launchctl", "bootstrap", "gui/501", plist]]
    assert garage_units.start_commands("darwin", 501, running=False, env=HOME) == [
        ["launchctl", "bootstrap", "gui/501",
         "/home/op/Library/LaunchAgents/dev.pitcall.garage.plist"]]
    assert garage_units.stop_commands("darwin", 501) == [
        ["launchctl", "bootout", "gui/501/dev.pitcall.garage"]]
    assert garage_units.status_command("darwin", 501) == [
        "launchctl", "print", "gui/501/dev.pitcall.garage"]


@pytest.mark.parametrize("call", [
    lambda: garage_units.start_commands("win32", 0, running=False),
    lambda: garage_units.stop_commands("win32", 0),
    lambda: garage_units.status_command("win32", 0),
])
def test_another_platform_is_refused(call):
    with pytest.raises(ValueError, match="Linux"):
        call()


def test_linger():
    assert garage_units.linger_enabled("Linger=yes\n") is True
    assert garage_units.linger_enabled("Linger=no\n") is False
    assert garage_units.linger_enabled("") is False
