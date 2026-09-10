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
        file_mod.main(["a title", "--milestone", "vNext - Some Milestone"])


def test_notes_routes_to_the_notes_milestone():
    """The whole point: a ruled exception is not deferred work and must not land
    in the milestone whose open count is read as work outstanding."""
    cmd = file_mod.build_command(
        {"backlog_milestone": "Backlog", "notes_milestone": "Notes"},
        "o/r", "t", None, notes=True,
    )
    assert cmd[cmd.index("--milestone") + 1] == "Notes"


def test_notes_refuses_rather_than_falling_back_to_the_backlog():
    """Silently filing a note into the backlog is the exact defect this exists to
    remove, so a missing key must be loud. Falling back would be indistinguishable
    from the old behaviour and nobody would learn the key was never set."""
    with pytest.raises(tracker.TrackerError, match="notes_milestone"):
        file_mod.build_command({"backlog_milestone": "Backlog"}, "o/r", "t", None,
                               notes=True)


def test_notes_refuses_on_an_explicit_null_too():
    """pitcall.config.example.json ships `"notes_milestone": null`, so a project
    that copied the example and never chose a value has the key PRESENT and empty.
    That is the likeliest real state, and it must refuse exactly like a missing
    key rather than passing None to `gh --milestone`."""
    with pytest.raises(tracker.TrackerError, match="notes_milestone"):
        file_mod.build_command({"backlog_milestone": "B", "notes_milestone": None},
                               "o/r", "t", None, notes=True)


def test_notes_is_a_boolean_not_a_milestone_name():
    """--notes selects between two milestones the CONFIG fixed; it cannot name one.
    That is what keeps test_there_is_no_milestone_override true: a flag able to
    express the forbidden thing is a flag someone eventually passes."""
    with pytest.raises(SystemExit):
        file_mod.main(["a title", "--notes", "vNext - Some Milestone"])


def test_without_notes_nothing_changes_for_a_project_that_has_no_notes_milestone():
    cmd = file_mod.build_command({"backlog_milestone": "Backlog"}, "o/r", "t", None)
    assert cmd[cmd.index("--milestone") + 1] == "Backlog"


def test_it_refuses_without_the_key_rather_than_guessing():
    with pytest.raises(tracker.TrackerError, match="backlog_milestone"):
        file_mod.build_command({}, "o/r", "t", None)


def test_body_file_is_optional():
    cmd = file_mod.build_command({"backlog_milestone": "B"}, "o/r", "t", None)
    assert "--body" in cmd and "--body-file" not in cmd
    cmd = file_mod.build_command({"backlog_milestone": "B"}, "o/r", "t", "b.md")
    assert cmd[cmd.index("--body-file") + 1] == "b.md"
