"""The garage's state file and `status` rendering (Task 8)."""

import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
import garage_state  # noqa: E402
from garage_state import days_left, read_state, render_status, write_state  # noqa: E402

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def state(**over):
    base = {
        "version": "0.17.0", "pid": 4242, "written_at": NOW, "config_error": None,
        "token_expiration": "2026-12-14 12:00:00 UTC", "paused_until": None,
        "runners": {
            "r10": {"state": "stopped"},
            "r1": {"state": "busy", "repo": "octo/app", "labels": ["self-hosted", "linux"],
                   "registration": "pitcall-3f9c2a71-r1-4", "job": "build", "error": None,
                   "retry_at": None, "since": NOW - 60},
            "r2": {"state": "broken", "error": "start exited 1", "retry_at": NOW + 150},
            "r3": {"state": "waiting", "repo": "octo/site"},
            "r4": {"state": "setup", "repo": "octo/site"},
        },
        "repos": {
            "octo/site": {"status": "setup failing", "reason": "exit 2: apt-get: not found",
                          "queued": 3, "labels": [["self-hosted", "linux"]], "retry_at": NOW + 60},
            "octo/app": {"status": "ok", "reason": None, "queued": 2,
                         "labels": [["self-hosted", "linux"], ["self-hosted", "large"]]},
            "octo/old": {"status": "blocked", "reason": "GitHub 404 on /repos/octo/old"},
            "octo/bad": {"status": "config invalid", "reason": "runner.env: PITCALL_X starts with a reserved prefix"},
        },
    }
    base.update(over)
    return base


def test_the_vocabulary_is_pinned():
    assert garage_state.RUNNER_STATES == (
        "stopped", "idle", "starting", "setup", "waiting", "busy", "cleaning", "broken")
    assert garage_state.REPO_STATUSES == ("ok", "blocked", "setup failing", "config invalid")


def test_write_then_read_round_trips_with_owner_only_mode(tmp_path):
    path = tmp_path / "state" / "pitcall" / "garage.json"
    write_state(path, state())
    assert read_state(path) == state()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in path.parent.iterdir()) == ["garage.json"]


def test_a_missing_or_broken_state_file_reads_as_none(tmp_path):
    assert read_state(tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("{")
    assert read_state(tmp_path / "bad.json") is None
    (tmp_path / "list.json").write_text("[]")
    assert read_state(tmp_path / "list.json") is None


def test_days_left_reads_both_header_forms():
    assert days_left("2026-12-14 12:00:00 UTC", NOW) == 30
    assert days_left("2026-11-21 13:00:00 +0100", NOW) == 7
    assert days_left("2026-11-13 12:00:00 UTC", NOW) == -1
    assert days_left(None, NOW) is None
    assert days_left("next tuesday", NOW) is None


def test_render_status_shows_every_runner_and_repo():
    out = render_status("running (code matches)", "~/.config/pitcall/garage.token (600)", state(), NOW)
    assert out.splitlines() == [
        "service: running (code matches)",
        "token: ~/.config/pitcall/garage.token (600) — expires in 30 days",
        "r1: busy octo/app build",
        "r2: broken — start exited 1 (retry in 3 min)",
        "r3: waiting for pickup octo/site",
        "r4: setup octo/site",
        "r10: stopped",
        "octo/app: ok, 2 queued, labels self-hosted+linux, self-hosted+large",
        "octo/bad: config invalid — runner.env: PITCALL_X starts with a reserved prefix; 0 queued, labels none",
        "octo/old: blocked — GitHub 404 on /repos/octo/old; 0 queued, labels none",
        "octo/site: setup failing — exit 2: apt-get: not found; 3 queued, labels self-hosted+linux (retry in 1 min)",
    ]


def test_a_token_expiring_within_seven_days_warns():
    out = render_status("running", "t", state(token_expiration="2026-11-20 12:00:00 UTC"), NOW)
    assert "token: t — expires in 6 days" in out
    assert "WARNING: the garage token expires in 6 days" in out
    assert "WARNING" not in render_status("running", "t", state(), NOW)
    assert "has expired" in render_status("running", "t", state(token_expiration="2026-11-01 00:00:00 UTC"), NOW)


def test_config_error_and_rate_limit_pause_are_shown():
    out = render_status("running", "t", state(config_error="runners: must be a whole number",
                                              paused_until=NOW + 300), NOW)
    assert "config: runners: must be a whole number (the service carries on with the last config it read)" in out
    assert f"paused: rate limited until {time.strftime('%H:%M:%S', time.localtime(NOW + 300))}" in out
    assert "paused" not in render_status("running", "t", state(paused_until=NOW - 1), NOW)


def test_status_without_a_state_file():
    out = render_status("not installed", "missing: no token file", None, NOW)
    assert out.splitlines() == ["service: not installed", "token: missing: no token file",
                                "runners: unknown (the service has not written its state)"]



def test_an_entry_that_is_not_an_object_is_shown_as_unreadable():
    out = render_status("running", "t", state(runners={"r1": "x"}, repos={"octo/app": 3}), NOW)
    assert "r1: unreadable" in out.splitlines()
    assert "octo/app: unreadable" in out.splitlines()
    out = render_status("running", "t", state(runners=["r1"], repos="octo/app"), NOW)
    assert "runners: none" in out and "repos: none joined" in out


def test_the_expiry_warning_says_a_new_token_needs_a_garage_start():
    out = render_status("running", "t", state(token_expiration="2026-11-20 12:00:00 UTC"), NOW)
    assert ("WARNING: the garage token expires in 6 days: create a new one, put it in the token "
            "file, then run garage start") in out.splitlines()
