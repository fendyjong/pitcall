"""The block verb: its refusals and the order of its steps, without a network."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import red_flag as red_flag_mod  # noqa: E402
import tracker  # noqa: E402

LABEL = "status:blocked"
CFG = {"status_labels": {"ongoing": "status:ongoing",
                         "in_review": "status:in-review",
                         "blocked": LABEL}}


def test_neither_a_reason_nor_clear_is_refused():
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        red_flag_mod.main(["58"])


def test_both_a_reason_and_clear_is_refused():
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        red_flag_mod.main(["58", "an upstream release", "--clear"])


def test_an_unset_label_is_refused_rather_than_guessed():
    for cfg in ({}, {"status_labels": {"ongoing": "status:ongoing"}}):
        with pytest.raises(tracker.TrackerError,
                           match="status_labels.blocked is not configured"):
            red_flag_mod.blocked_label(cfg)


def test_a_blank_reason_is_refused():
    with pytest.raises(tracker.TrackerError, match="blank"):
        red_flag_mod.mark_commands(LABEL, "o/r", 58, "open", "   ")


def test_marking_an_issue_that_is_not_open_is_refused():
    with pytest.raises(tracker.TrackerError, match="#58 is closed"):
        red_flag_mod.mark_commands(LABEL, "o/r", 58, "CLOSED", "an upstream release")


def test_marking_puts_the_label_on_before_the_reason():
    edit, comment = red_flag_mod.mark_commands(LABEL, "o/r", 58, "open",
                                            "  an upstream release  ")
    assert edit == ["gh", "issue", "edit", "58", "--repo", "o/r",
                    "--add-label", LABEL]
    assert comment[:6] == ["gh", "issue", "comment", "58", "--repo", "o/r"]
    assert comment[comment.index("--body") + 1] == (
        tracker.BLOCK_MARKER + "an upstream release")


def test_clearing_removes_the_blocked_label_and_nothing_else():
    cmd = red_flag_mod.clear_command(LABEL, "o/r", 58, ["status:ongoing", LABEL])
    assert cmd == ["gh", "issue", "edit", "58", "--repo", "o/r",
                   "--remove-label", LABEL]


def test_clearing_an_issue_that_is_not_blocked_is_nothing_to_do():
    assert red_flag_mod.clear_command(LABEL, "o/r", 58, ["status:ongoing"]) is None


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
    red_flag_mod.main(["58", "--clear"])
    assert ran == [["gh", "issue", "edit", "58", "--repo", "o/r",
                    "--remove-label", LABEL]]


def test_clearing_an_unblocked_issue_never_calls_gh_issue_edit(monkeypatch, capsys):
    ran = _stub(monkeypatch, labels=["status:ongoing"])
    red_flag_mod.main(["58", "--clear"])
    assert ran == []
    assert "#58 is not blocked" in capsys.readouterr().out


def test_marking_never_touches_the_claim_labels(monkeypatch, capsys):
    """Blocking is not unclaiming: `ongoing` stays, so nobody claims it twice."""
    ran = _stub(monkeypatch, labels=["status:ongoing"])
    red_flag_mod.main(["58", "an upstream release"])
    assert [c[:3] for c in ran] == [["gh", "issue", "edit"],
                                    ["gh", "issue", "comment"]]
    flat = " ".join(" ".join(c) for c in ran)
    assert "status:ongoing" not in flat and "status:in-review" not in flat
    assert "--remove-label" not in flat
    assert "#58 is blocked on: an upstream release" in capsys.readouterr().out


# --- the two partial-failure messages, and the unset label in main -----------

def _failing_run(monkeypatch, fail_on):
    """Stub `tracker.run` to raise on call number `fail_on` (1-based)."""
    calls = []

    def run(*args, cwd=None):
        calls.append(list(args))
        if len(calls) == fail_on:
            raise tracker.TrackerError(f"{' '.join(args)} failed: boom")
        return ""

    monkeypatch.setattr(tracker, "run", run)
    return calls


def test_a_refused_label_says_nothing_else_happened(monkeypatch):
    _stub(monkeypatch)
    calls = _failing_run(monkeypatch, fail_on=1)
    with pytest.raises(tracker.TrackerError,
                       match="nothing else has happened yet for #58; safe to re-run"):
        red_flag_mod.main(["58", "an upstream release"])
    assert len(calls) == 1, "the reason must not be posted after a refused label"


def test_a_failed_comment_says_the_reason_was_not_posted(monkeypatch):
    """On a re-mark the PREVIOUS reason is still the latest comment, so
    "no reason recorded" would be false -- claim shows the old reason."""
    _stub(monkeypatch, labels=[LABEL])
    _failing_run(monkeypatch, fail_on=2)
    with pytest.raises(tracker.TrackerError) as exc:
        red_flag_mod.main(["58", "an upstream release"])
    msg = str(exc.value)
    assert f"{LABEL} is on #58, but the reason was not posted" in msg
    assert "claim shows the previous reason, if any" in msg
    assert "no reason recorded" not in msg


def test_main_refuses_an_unset_label_before_any_network_call(monkeypatch):
    ran = _stub(monkeypatch)
    monkeypatch.setattr(tracker, "config",
                        lambda checkout=None: {"status_labels": {"ongoing": "status:ongoing"}})

    def no_network(cwd=None):
        raise AssertionError("origin_repo called before the label was checked")

    monkeypatch.setattr(tracker, "origin_repo", no_network)
    with pytest.raises(tracker.TrackerError,
                       match="status_labels.blocked is not configured"):
        red_flag_mod.main(["58", "an upstream release"])
    assert ran == []


def test_errors_name_the_red_flag_command():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "red_flag.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("usage: red-flag ")
