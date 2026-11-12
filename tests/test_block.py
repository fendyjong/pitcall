"""The block verb: its refusals and the order of its steps, without a network."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import block as block_mod  # noqa: E402
import tracker  # noqa: E402

LABEL = "status:blocked"
CFG = {"status_labels": {"ongoing": "status:ongoing",
                         "in_review": "status:in-review",
                         "blocked": LABEL}}


def test_neither_a_reason_nor_clear_is_refused():
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        block_mod.main(["58"])


def test_both_a_reason_and_clear_is_refused():
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        block_mod.main(["58", "an upstream release", "--clear"])


def test_an_unset_label_is_refused_rather_than_guessed():
    for cfg in ({}, {"status_labels": {"ongoing": "status:ongoing"}}):
        with pytest.raises(tracker.TrackerError,
                           match="status_labels.blocked is not configured"):
            block_mod.blocked_label(cfg)


def test_a_blank_reason_is_refused():
    with pytest.raises(tracker.TrackerError, match="blank"):
        block_mod.mark_commands(LABEL, "o/r", 58, "open", "   ")


def test_marking_an_issue_that_is_not_open_is_refused():
    with pytest.raises(tracker.TrackerError, match="#58 is closed"):
        block_mod.mark_commands(LABEL, "o/r", 58, "CLOSED", "an upstream release")


def test_marking_puts_the_label_on_before_the_reason():
    edit, comment = block_mod.mark_commands(LABEL, "o/r", 58, "open",
                                            "  an upstream release  ")
    assert edit == ["gh", "issue", "edit", "58", "--repo", "o/r",
                    "--add-label", LABEL]
    assert comment[:6] == ["gh", "issue", "comment", "58", "--repo", "o/r"]
    assert comment[comment.index("--body") + 1] == (
        tracker.BLOCK_MARKER + "an upstream release")


def test_clearing_removes_the_blocked_label_and_nothing_else():
    cmd = block_mod.clear_command(LABEL, "o/r", 58, ["status:ongoing", LABEL])
    assert cmd == ["gh", "issue", "edit", "58", "--repo", "o/r",
                   "--remove-label", LABEL]


def test_clearing_an_issue_that_is_not_blocked_is_nothing_to_do():
    assert block_mod.clear_command(LABEL, "o/r", 58, ["status:ongoing"]) is None


# --- main, with the network stubbed: what actually runs ----------------------

def _stub(monkeypatch, state="open", labels=()):
    ran = []
    monkeypatch.setattr(tracker, "config", lambda checkout=None: CFG)
    monkeypatch.setattr(tracker, "origin_repo", lambda cwd=None: "o/r")
    monkeypatch.setattr(tracker, "issue_state", lambda issue, repo: {
        "state": state, "title": "t", "labels": list(labels)})
    monkeypatch.setattr(tracker, "run",
                        lambda *args, cwd=None: ran.append(list(args)) or "")
    return ran


def test_clearing_works_on_a_closed_issue(monkeypatch):
    ran = _stub(monkeypatch, state="closed", labels=[LABEL, "status:ongoing"])
    block_mod.main(["58", "--clear"])
    assert ran == [["gh", "issue", "edit", "58", "--repo", "o/r",
                    "--remove-label", LABEL]]


def test_clearing_an_unblocked_issue_never_calls_gh_issue_edit(monkeypatch, capsys):
    ran = _stub(monkeypatch, labels=["status:ongoing"])
    block_mod.main(["58", "--clear"])
    assert ran == []
    assert "#58 is not blocked" in capsys.readouterr().out


def test_marking_never_touches_the_claim_labels(monkeypatch, capsys):
    """Blocking is not unclaiming: `ongoing` stays, so nobody claims it twice."""
    ran = _stub(monkeypatch, labels=["status:ongoing"])
    block_mod.main(["58", "an upstream release"])
    assert [c[:3] for c in ran] == [["gh", "issue", "edit"],
                                    ["gh", "issue", "comment"]]
    flat = " ".join(" ".join(c) for c in ran)
    assert "status:ongoing" not in flat and "status:in-review" not in flat
    assert "--remove-label" not in flat
    assert "#58 is blocked on: an upstream release" in capsys.readouterr().out
