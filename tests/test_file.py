"""The file verb: routing is the whole point, so routing is what is pinned."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import file as file_mod  # noqa: E402
import tracker  # noqa: E402


def test_the_milestone_is_always_the_backlog_one():
    cmd = file_mod.build_command({"backlog_milestone": "Backlog"}, "o/r", "t", None)
    assert cmd[cmd.index("--milestone") + 1] == "Backlog"


def test_there_is_no_milestone_override():
    """A flag able to express the forbidden thing is a flag someone passes."""
    with pytest.raises(SystemExit):
        file_mod.main(["a title", "--milestone", "v0.9.x - MVP: Launch Day"])


def test_it_refuses_without_the_key_rather_than_guessing():
    with pytest.raises(tracker.TrackerError, match="backlog_milestone"):
        file_mod.build_command({}, "o/r", "t", None)


def test_body_file_is_optional():
    cmd = file_mod.build_command({"backlog_milestone": "B"}, "o/r", "t", None)
    assert "--body" in cmd and "--body-file" not in cmd
    cmd = file_mod.build_command({"backlog_milestone": "B"}, "o/r", "t", "b.md")
    assert cmd[cmd.index("--body-file") + 1] == "b.md"
