"""scripts/garage.py: the garage's CLI, with every service-manager call faked.

Every path comes from an injected `env` pointing into tmp_path, and every systemctl,
launchctl and loginctl call goes through `FakeRun`, so nothing here touches the real home
or the real service manager.
"""

import importlib.util
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("garage_cli", ROOT / "scripts" / "garage.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cli  # dataclasses looks the module up here on Python 3.12
_spec.loader.exec_module(cli)
VERSION = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]


class FakeRun:
    """Records each argv; answers the way the service manager would."""

    def __init__(self, active=False, linger="Linger=no\n", fail=None):
        self.calls, self.active, self.linger, self.fail = [], active, linger, fail

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if self.fail and self.fail in argv:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        if argv[:3] == ["systemctl", "--user", "is-active"] or argv[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(argv, 0 if self.active else 3, "", "")
        if argv[0] == "loginctl":
            return subprocess.CompletedProcess(argv, 0, self.linger, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def home(tmp_path):
    env = {
        "HOME": str(tmp_path / "home"),
        "USER": "tester",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    token = tmp_path / "token"
    token.write_text("test-token\n")
    token.chmod(0o600)
    return env, token


def write_config(env, token, repos=None):
    path = Path(env["XDG_CONFIG_HOME"]) / "pitcall" / "garage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "runners": 2,
        "recipe": {"start": "true", "stop": "true", "exec": "true", "cleanup": "true"},
        "token_file": str(token),
        "repos": repos or {},
    }))
    return path


def installed(env):
    return Path(env["XDG_DATA_HOME"]) / "pitcall" / "garage"


def write_busy_state(env):
    path = Path(env["XDG_STATE_HOME"]) / "pitcall" / "garage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    runner = {"labels": None, "registration": None, "job": None, "error": None,
              "retry_at": None, "since": 0.0}
    path.write_text(json.dumps({
        "version": VERSION, "pid": 1, "written_at": 0.0, "config_error": None,
        "token_expiration": None, "paused_until": None,
        "runners": {
            "r1": {**runner, "state": "busy", "repo": "octo/app",
                   "labels": ["self-hosted", "linux"],
                   "registration": "pitcall-3f9c2a71-r1-1", "job": "build"},
            "r2": {**runner, "state": "idle", "repo": None},
        },
        "repos": {},
    }))


def garage(env, *argv, run=None, platform="linux"):
    return cli.main(list(argv), env=env, run=run or FakeRun(), platform=platform, uid=1000)


@pytest.mark.parametrize("argv", [["start"], ["stop"], ["join", "--repo", "octo/app"],
                                  ["leave", "--repo", "octo/app"]])
def test_every_command_but_status_refuses_without_a_machine_config(home, capsys, argv):
    env, _ = home
    run = FakeRun()
    assert garage(env, *argv, run=run) == 1
    err = capsys.readouterr().err
    assert "copy" in err and "garage.example.json" in err
    assert run.calls == []


def test_status_works_without_a_machine_config(home, capsys):
    env, _ = home
    assert garage(env, "status") == 0
    out = capsys.readouterr().out
    assert "garage.example.json" in out
    assert "service: not installed" in out


def test_start_refuses_a_token_file_others_can_read(home, capsys):
    env, token = home
    write_config(env, token)
    token.chmod(0o644)
    run = FakeRun()
    assert garage(env, "start", run=run) == 1
    assert "chmod 600" in capsys.readouterr().err
    assert run.calls == []
    assert not installed(env).exists()


def test_start_writes_the_id_installs_the_code_and_enables_the_unit(home):
    env, token = home
    config = write_config(env, token)
    run = FakeRun()
    assert garage(env, "start", run=run) == 0
    assert re.fullmatch(r"[0-9a-f]{8}", json.loads(config.read_text())["id"])
    code = installed(env)
    assert (code / "garage_service.py").is_file()
    assert (code / "VERSION").read_text().strip() == VERSION
    assert os.access(code / "recipes" / "docker" / "start.sh", os.X_OK)
    assert not list(code.rglob("__pycache__"))
    unit = (Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user" / "pitcall-garage.service").read_text()
    assert f"ExecStart={sys.executable} {code / 'garage_service.py'}" in unit
    assert ["systemctl", "--user", "enable", "--now", "pitcall-garage"] in run.calls
    assert not any("restart" in argv for argv in run.calls)


def test_a_second_start_keeps_the_id_and_refreshes_the_code(home):
    env, token = home
    config = write_config(env, token)
    assert garage(env, "start") == 0
    first = json.loads(config.read_text())["id"]
    stale = installed(env) / "stale.txt"
    stale.write_text("left by an older install")
    assert garage(env, "start") == 0
    assert json.loads(config.read_text())["id"] == first
    assert not stale.exists()


def test_start_on_a_running_garage_names_its_jobs_then_restarts(home, capsys):
    env, token = home
    write_config(env, token)
    write_busy_state(env)
    run = FakeRun(active=True)
    assert garage(env, "start", run=run) == 0
    out = capsys.readouterr().out
    assert "r1: octo/app build" in out
    assert "r2:" not in out
    assert ["systemctl", "--user", "restart", "pitcall-garage"] in run.calls


def test_start_advises_lingering_and_never_enables_it(home, capsys):
    env, token = home
    write_config(env, token)
    run = FakeRun(linger="Linger=no\n")
    assert garage(env, "start", run=run) == 0
    assert "loginctl enable-linger tester" in capsys.readouterr().out
    assert ["loginctl", "show-user", "tester", "-p", "Linger"] in run.calls
    assert not any("enable-linger" in argv for argv in run.calls)


def test_start_says_nothing_about_lingering_when_it_is_on(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start", run=FakeRun(linger="Linger=yes\n")) == 0
    assert "enable-linger" not in capsys.readouterr().out


def test_start_on_macos_writes_the_plist_and_bootstraps_it(home):
    env, token = home
    write_config(env, token)
    run = FakeRun()
    assert garage(env, "start", run=run, platform="darwin") == 0
    plist = (Path(env["HOME"]) / "Library" / "LaunchAgents" / "dev.pitcall.garage.plist").read_text()
    assert "ExitTimeOut" in plist and "garage_service.py" in plist
    plist_path = str(Path(env["HOME"]) / "Library" / "LaunchAgents" / "dev.pitcall.garage.plist")
    assert [argv for argv in run.calls if argv[0] == "launchctl"] == [
        ["launchctl", "print", "gui/1000/dev.pitcall.garage"],
        ["launchctl", "enable", "gui/1000/dev.pitcall.garage"],
        ["launchctl", "bootstrap", "gui/1000", plist_path]]
    assert not any(argv[0] == "loginctl" for argv in run.calls)


def test_start_refuses_an_unsupported_platform(home, capsys):
    env, token = home
    write_config(env, token)
    run = FakeRun()
    assert garage(env, "start", run=run, platform="win32") == 1
    assert "Linux" in capsys.readouterr().err
    assert run.calls == []


def test_a_failing_service_manager_command_is_reported(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start", run=FakeRun(fail="enable")) == 1
    err = capsys.readouterr().err
    assert "systemctl --user enable --now pitcall-garage" in err and "boom" in err


def test_stop_names_the_jobs_it_ends_and_disables_the_service(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    write_busy_state(env)
    capsys.readouterr()
    run = FakeRun(active=True)
    assert garage(env, "stop", run=run) == 0
    assert "r1: octo/app build" in capsys.readouterr().out
    assert run.calls == [["systemctl", "--user", "is-active", "pitcall-garage"],
                         ["systemctl", "--user", "disable", "--now", "pitcall-garage"]]


def test_status_shows_a_running_garage_whose_code_matches(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    write_busy_state(env)
    capsys.readouterr()
    assert garage(env, "status", run=FakeRun(active=True)) == 0
    out = capsys.readouterr().out
    assert "service: running" in out
    assert "matches" in out
    assert "r1" in out and "octo/app" in out


def test_status_shows_a_stopped_service(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    capsys.readouterr()
    assert garage(env, "status", run=FakeRun(active=False)) == 0
    assert "service: stopped" in capsys.readouterr().out


def test_status_names_an_outdated_install(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    (installed(env) / "VERSION").write_text("0.0.1\n")
    capsys.readouterr()
    assert garage(env, "status", run=FakeRun(active=True)) == 0
    assert f"installed 0.0.1, plugin {VERSION}: run garage start" in capsys.readouterr().out


def test_status_flags_an_unsafe_token_file(home, capsys):
    env, token = home
    write_config(env, token)
    token.chmod(0o640)
    assert garage(env, "status") == 0
    assert "chmod 600" in capsys.readouterr().out


def test_join_reads_the_token_and_hands_the_checkout_repo_to_join(home, monkeypatch):
    env, token = home
    config = write_config(env, token)
    seen = {}
    monkeypatch.setattr(cli.tracker, "origin_repo", lambda cwd=None: "octo/app")
    monkeypatch.setattr(cli.garage_github, "GitHub", lambda tok: ("api", tok))

    def fake_join(api, repo, config_path, labels_arg, allow_public, yes, out=print):
        seen.update(api=api, repo=repo, config_path=config_path, labels=labels_arg,
                    public=allow_public, yes=yes)
        return 0

    monkeypatch.setattr(cli.garage_join, "run_join", fake_join)
    assert garage(env, "join", "--labels", "self-hosted,linux") == 0
    assert seen == {"api": ("api", "test-token"), "repo": "octo/app", "config_path": config,
                    "labels": "self-hosted,linux", "public": False, "yes": False}


def test_join_passes_the_repo_allow_public_and_yes(home, monkeypatch):
    env, token = home
    write_config(env, token)
    seen = {}
    monkeypatch.setattr(cli.garage_github, "GitHub", lambda tok: "api")
    monkeypatch.setattr(cli.garage_join, "run_join",
                        lambda api, repo, path, labels, public, yes, out=print:
                        seen.update(repo=repo, public=public, yes=yes) or 0)
    assert garage(env, "join", "--repo", "octo/site", "--allow-public", "--yes") == 0
    assert seen == {"repo": "octo/site", "public": True, "yes": True}


def test_join_refuses_an_unsafe_token_file(home, capsys, monkeypatch):
    env, token = home
    write_config(env, token)
    token.chmod(0o604)

    def never(*args, **kwargs):
        raise AssertionError("join must not run with an unsafe token file")

    monkeypatch.setattr(cli.garage_join, "run_join", never)
    assert garage(env, "join", "--repo", "octo/app") == 1
    assert "chmod 600" in capsys.readouterr().err


def test_join_outside_a_checkout_says_to_pass_a_repo(home, capsys, monkeypatch):
    env, token = home
    write_config(env, token)

    def no_repo(cwd=None):
        raise cli.tracker.TrackerError("gh repo view failed")

    monkeypatch.setattr(cli.tracker, "origin_repo", no_repo)
    assert garage(env, "join") == 1
    assert "--repo owner/name" in capsys.readouterr().err


def test_leave_removes_the_repo(home):
    env, token = home
    config = write_config(env, token, repos={"octo/app": {"labels": [["self-hosted", "linux"]]}})
    assert garage(env, "leave", "--repo", "octo/app") == 0
    assert json.loads(config.read_text())["repos"] == {}


# --- sweep (#59) -------------------------------------------------------------------------------


def unit_text(env):
    unit = Path(env["XDG_CONFIG_HOME"]) / "systemd" / "user" / "pitcall-garage.service"
    return unit.read_text()


def test_start_gives_the_service_the_path_of_the_shell_that_ran_it(home):
    env, token = home
    env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin"
    write_config(env, token)
    assert garage(env, "start") == 0
    assert "Environment=PATH=/opt/homebrew/bin:/usr/bin:/bin\n" in unit_text(env)
    assert garage(env, "start", platform="darwin") == 0
    plist = Path(env["HOME"]) / "Library" / "LaunchAgents" / "dev.pitcall.garage.plist"
    assert plistlib.loads(plist.read_bytes())["EnvironmentVariables"] == {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin"}


def test_stop_names_no_jobs_from_the_state_file_a_stopped_service_left(home, capsys):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    write_busy_state(env)
    capsys.readouterr()
    assert garage(env, "stop", run=FakeRun(active=False)) == 0
    assert "octo/app" not in capsys.readouterr().out


def test_stop_on_a_garage_never_started_says_so_and_succeeds(home, capsys):
    env, token = home
    write_config(env, token)
    run = FakeRun(fail="disable")  # systemctl refuses to disable a unit file that does not exist
    assert garage(env, "stop", run=run) == 0
    assert "not installed" in capsys.readouterr().out and run.calls == []


def test_a_second_stop_on_macos_succeeds_without_booting_out_again(home):
    env, token = home
    write_config(env, token)
    assert garage(env, "start", platform="darwin") == 0
    run = FakeRun(active=False, fail="bootout")  # launchctl refuses an agent it has not loaded
    assert garage(env, "stop", run=run, platform="darwin") == 0
    assert ["launchctl", "disable", "gui/1000/dev.pitcall.garage"] in run.calls
    assert not any("bootout" in argv for argv in run.calls)


@pytest.mark.parametrize("command", ["join", "leave"])
def test_join_and_leave_without_gh_say_to_pass_a_repo(home, capsys, monkeypatch, command):
    env, token = home
    write_config(env, token)

    def no_gh(cwd=None):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(cli.tracker, "origin_repo", no_gh)
    assert garage(env, command) == 1
    assert "--repo owner/name" in capsys.readouterr().err


def test_a_start_that_fails_leaves_the_code_that_last_ran(home, capsys, monkeypatch):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    (installed(env) / "VERSION").write_text("0.16.0\n")  # stands in for the code that last ran
    assert garage(env, "start", run=FakeRun(fail="enable")) == 1  # the service manager refuses
    assert (installed(env) / "VERSION").read_text() == "0.16.0\n"
    assert sorted(p.name for p in installed(env).parent.iterdir()) == ["garage"]

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli.shutil, "copytree", disk_full)
    capsys.readouterr()
    assert garage(env, "start") == 1  # the copy itself fails
    assert "No space left on device" in capsys.readouterr().err
    assert (installed(env) / "VERSION").read_text() == "0.16.0\n"


def test_start_names_the_jobs_before_it_restarts(home, capsys):
    env, token = home
    write_config(env, token)
    write_busy_state(env)
    printed_before_restart = []

    class Watching(FakeRun):
        def __call__(self, argv, **kwargs):
            if "restart" in argv:
                printed_before_restart.append(capsys.readouterr().out)
            return super().__call__(argv, **kwargs)

    assert garage(env, "start", run=Watching(active=True)) == 0
    assert "r1: octo/app build" in printed_before_restart[0]


def test_status_ends_in_one_newline_and_the_token_is_in_no_call_output_or_unit(home, capsys):
    env, token = home
    write_config(env, token)
    run, printed = FakeRun(active=True), {}
    for command in ("start", "status", "stop"):
        assert garage(env, command, run=run) == 0
        printed[command] = capsys.readouterr()
    assert printed["status"].out.endswith("\n") and not printed["status"].out.endswith("\n\n")
    assert not any("test-token" in " ".join(argv) for argv in run.calls)
    assert not any("test-token" in out.out + out.err for out in printed.values())
    assert "test-token" not in unit_text(env)


@pytest.mark.parametrize("runners", [{"r1": "x", "r2": {"state": "busy", "repo": "octo/app",
                                                        "job": "build"}}, ["r1"]],
                         ids=["an-entry-not-an-object", "runners-not-an-object"])
def test_stop_reads_a_state_file_whose_runners_are_not_objects(home, capsys, runners):
    env, token = home
    write_config(env, token)
    assert garage(env, "start") == 0
    state = Path(env["XDG_STATE_HOME"]) / "pitcall" / "garage.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"runners": runners}))
    capsys.readouterr()
    assert garage(env, "stop", run=FakeRun(active=True)) == 0
    named = isinstance(runners, dict)
    assert ("r2: octo/app build" in capsys.readouterr().out) is named
