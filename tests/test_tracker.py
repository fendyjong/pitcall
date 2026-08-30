"""The mechanism behind the issue verbs, exercised without a network."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import tracker  # noqa: E402

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def test_slug_is_deterministic_and_bounded():
    assert tracker.slug("Build the claim and file verbs!") == "build-the-claim-and-file-verbs"
    assert tracker.slug("A" * 80) == "a" * 40
    assert tracker.slug("--- weird!!! ---") == "weird"


def test_branch_name_does_not_double_the_separator():
    cfg = {"branch_prefix": "feat/"}
    assert tracker.branch_name(cfg, 19, "Claim and file") == "feat/19-claim-and-file"
    assert "//" not in tracker.branch_name(cfg, 19, "Claim and file")


def test_branch_name_refuses_without_a_prefix():
    with pytest.raises(tracker.TrackerError, match="branch_prefix"):
        tracker.branch_name({}, 19, "x")


def test_status_label_absent_is_none_not_a_guess():
    assert tracker.status_label({}, "ongoing") is None
    assert tracker.status_label({"status_labels": {}}, "ongoing") is None
    assert tracker.status_label({"status_labels": {"ongoing": "s:o"}}, "ongoing") == "s:o"


# --- staleness: both directions, offline ------------------------------------


def test_a_stale_claim_on_an_inactive_branch_is_stale():
    assert tracker.is_stale(NOW - timedelta(hours=25), 0, 24, NOW) == "stale"


def test_a_stale_claim_on_an_ACTIVE_branch_is_live():
    """Commit-recency alone never expires; this is the half that protects work."""
    assert tracker.is_stale(NOW - timedelta(hours=25), 3, 24, NOW) == "live"


def test_a_FRESH_claim_on_an_inactive_branch_is_live():
    """Age alone would steal a task that legitimately runs long."""
    assert tracker.is_stale(NOW - timedelta(hours=1), 0, 24, NOW) == "live"


def test_a_FRESH_claim_on_an_ACTIVE_branch_is_live():
    """The fourth corner. Obvious, and pinned anyway: the 2x2 is the whole rule,
    and a corner with no assertion is where a refactor quietly changes it."""
    assert tracker.is_stale(NOW - timedelta(hours=1), 3, 24, NOW) == "live"


def test_the_boundary_is_inclusive():
    assert tracker.is_stale(NOW - timedelta(hours=24), 0, 24, NOW) == "stale"


def test_run_raises_rather_than_returning_empty():
    with pytest.raises(tracker.TrackerError, match="failed"):
        tracker.run("git", "rev-parse", "definitely-not-a-ref", cwd=str(ROOT))


# --- naive datetimes are refused, not guessed at -----------------------------

_A_NAIVE_DATETIME = datetime(2026, 8, 30, 12, 0)  # no tzinfo -- the trap


def test_is_stale_refuses_a_naive_now():
    with pytest.raises(tracker.TrackerError, match="now"):
        tracker.is_stale(NOW - timedelta(hours=25), 0, 24, _A_NAIVE_DATETIME)


def test_is_stale_refuses_a_naive_comment_time():
    with pytest.raises(tracker.TrackerError, match="comment_time"):
        tracker.is_stale(_A_NAIVE_DATETIME, 0, 24, NOW)


def test_is_stale_still_works_with_aware_datetimes():
    """Without this, the guard could reject everything and no other test
    would notice -- every other is_stale test already passes tz-aware values,
    but none of them exists to prove the guard is not simply "always raise"."""
    assert tracker.is_stale(NOW - timedelta(hours=25), 0, 24, NOW) == "stale"


def test_commits_since_refuses_a_naive_since(tmp_path):
    """Uses a real repo with a real commit past the base -- with the guard
    removed, this is what actually reaches the date comparison and raises
    the bare TypeError the guard exists to intercept; a fake branch/base
    would fail on the git call first and never prove the guard did anything."""
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(("git", "-C", str(repo), *a), check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "a").write_text("1\n")
    run("add", "a")
    run("commit", "-qm", "base")
    run("branch", "work")
    run("checkout", "-q", "work")
    (repo / "c").write_text("3\n")
    run("add", "c")
    run("commit", "-qm", "real work")
    with pytest.raises(tracker.TrackerError, match="since"):
        tracker.commits_since("work", "main", _A_NAIVE_DATETIME, cwd=str(repo))


def test_commits_since_still_works_with_an_aware_since(tmp_path):
    """The paired positive: an aware `since` must still reach git, not just
    pass the guard. Reuses the same repo shape as the base-range test."""
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(("git", "-C", str(repo), *a), check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "a").write_text("1\n")
    run("add", "a")
    run("commit", "-qm", "base")
    run("branch", "work")
    run("checkout", "-q", "work")
    (repo / "c").write_text("3\n")
    run("add", "c")
    run("commit", "-qm", "real work")
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert tracker.commits_since("work", "main", long_ago, cwd=str(repo)) == 1


# --- config() translates lane_config's bare RuntimeError --------------------


def test_config_translates_a_missing_required_key_into_a_TrackerError(tmp_path):
    """`lane_config.load_config` raises a bare `RuntimeError` here, which a
    caller doing `except TrackerError` would not catch -- the whole point of
    this test is that `tracker.config()` must not let that escape unwrapped."""
    (tmp_path / "pitcall.config.json").write_text('{"bringup": "x"}')
    with pytest.raises(tracker.TrackerError, match="missing required key"):
        tracker.config(tmp_path)


def test_commits_since_counts_only_commits_past_the_base(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a: subprocess.run(("git", "-C", str(repo), *a), check=True,
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "a").write_text("1\n")
    run("add", "a")
    run("commit", "-qm", "base")
    run("branch", "work")
    (repo / "b").write_text("2\n")
    run("add", "b")
    run("commit", "-qm", "on main only")
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
    # `work` has nothing past `main`'s branch point, so a naive `git log work`
    # would count the base commit and report activity that is not there.
    assert tracker.commits_since("work", "main", long_ago, cwd=str(repo)) == 0
    run("checkout", "-q", "work")
    (repo / "c").write_text("3\n")
    run("add", "c")
    run("commit", "-qm", "real work")
    assert tracker.commits_since("work", "main", long_ago, cwd=str(repo)) == 1
