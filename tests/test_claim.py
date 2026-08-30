"""The claim verb's decisions, exercised without a network."""

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import claim as claim_mod  # noqa: E402
import tracker  # noqa: E402

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
CFG = {"claim_expiry_hours": 24, "branch_prefix": "feat/", "default_branch": "main"}


def _repo(tmp_path, commits_on_work=0):
    repo = tmp_path / "r"
    repo.mkdir()
    def g(*a):
        subprocess.run(("git", "-C", str(repo), *a), check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (repo / "a").write_text("1\n")
    g("add", "a")
    g("commit", "-qm", "base")
    g("branch", "feat/19-x")
    for i in range(commits_on_work):
        g("checkout", "-q", "feat/19-x")
        (repo / f"w{i}").write_text("x\n")
        g("add", f"w{i}")
        g("commit", "-qm", f"work {i}")
    return repo


def _repo_remote_only(tmp_path, commits_on_work=0):
    """Same shape as `_repo`, but issue 19's branch exists ONLY as a
    remote-tracking ref -- the shape of a clone that has fetched `origin` but
    never checked the branch out locally, which is every clone but the one
    that ran `claim`, since `claim` never pushes what it cuts."""
    repo = tmp_path / "r"
    repo.mkdir()
    def g(*a):
        subprocess.run(("git", "-C", str(repo), *a), check=True, capture_output=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (repo / "a").write_text("1\n")
    g("add", "a")
    g("commit", "-qm", "base")
    g("branch", "feat/19-x")
    g("checkout", "-q", "feat/19-x")
    for i in range(commits_on_work):
        (repo / f"w{i}").write_text("x\n")
        g("add", f"w{i}")
        g("commit", "-qm", f"work {i}")
    g("checkout", "-q", "main")
    sha = subprocess.run(("git", "-C", str(repo), "rev-parse", "feat/19-x"),
                         check=True, capture_output=True, text=True).stdout.strip()
    g("branch", "-D", "feat/19-x")
    g("update-ref", "refs/remotes/origin/feat/19-x", sha)
    return repo


def _claim(hours_ago, body=None):
    when = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"created_at": when, "body": body or tracker.CLAIM_MARKER + "https://example/s"}


def test_a_stale_claim_on_an_inactive_branch_reads_stale(tmp_path):
    repo = _repo(tmp_path)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), 19, "main", NOW, cwd=str(repo))
    assert verdict == "stale"
    assert "EXPIRED" in lines[0] and "INACTIVE" in lines[1]


def test_a_stale_claim_on_an_ACTIVE_branch_reads_live(tmp_path):
    repo = _repo(tmp_path, commits_on_work=2)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), 19, "main", NOW, cwd=str(repo))
    assert verdict == "live"
    assert "EXPIRED" in lines[0], "the age half held independently"
    assert "ACTIVE" in lines[1]


def test_a_fresh_claim_on_an_inactive_branch_reads_live(tmp_path):
    repo = _repo(tmp_path)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(1), 19, "main", NOW, cwd=str(repo))
    assert verdict == "live"
    assert "LIVE" in lines[0] and "INACTIVE" in lines[1]


def test_a_missing_branch_counts_as_no_activity(tmp_path):
    """A claim comment with no branch expires cleanly -- the safe failure."""
    repo = _repo(tmp_path)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), 999, "main", NOW, cwd=str(repo))
    assert verdict == "stale"
    assert "no branch found locally or on origin" in lines[1]


def test_activity_is_measured_against_a_remote_tracking_ref_too(tmp_path):
    """I2's regression: with real work on ONLY `origin/feat/19-x` (no local
    ref at all), the activity half must still see it -- a plain
    `git branch --list` reading this as "no branch" is exactly how a stale
    claim on a genuinely active branch reads INACTIVE and lets --take steal
    live work."""
    repo = _repo_remote_only(tmp_path, commits_on_work=2)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), 19, "main", NOW, cwd=str(repo))
    assert verdict == "live"
    assert "ACTIVE" in lines[1]
    assert "origin/feat/19-x" in lines[1]


def test_a_missing_expiry_key_refuses_instead_of_crashing(tmp_path):
    """claim_expiry_hours is not in REQUIRED_KEYS, so a config can lack it."""
    repo = _repo(tmp_path)
    cfg = {k: v for k, v in CFG.items() if k != "claim_expiry_hours"}
    with pytest.raises(tracker.TrackerError, match="claim_expiry_hours"):
        claim_mod.describe_claim(cfg, _claim(25), 19, "main", NOW,
                                 cwd=str(repo))


def test_there_is_no_flag_for_adopting_an_unclaimed_branch():
    """The refusal is deliberate, so an --adopt style escape must not parse.

    SystemExit comes from argparse, before any network call -- which also pins
    that parsing happens before the config load.
    """
    with pytest.raises(SystemExit):
        claim_mod.main(["19", "--adopt"])


# --- I3: a re-run of the SAME session resumes instead of refusing -----------


def test_resumes_own_claim_matches_the_sessions_own_marker():
    claim = _claim(1, body=tracker.CLAIM_MARKER + "https://example/session-42")
    assert claim_mod.resumes_own_claim("https://example/session-42", claim)


def test_resumes_own_claim_is_false_for_someone_elses_session():
    claim = _claim(1, body=tracker.CLAIM_MARKER + "https://example/session-42")
    assert not claim_mod.resumes_own_claim("https://example/some-other-session", claim)


def test_resumes_own_claim_never_matches_an_empty_session():
    """An empty string is a substring of every string -- without this guard,
    every unattributed re-run (no --session at all) would look like a resume
    of a claim that is not its own."""
    claim = _claim(1, body=tracker.CLAIM_MARKER + "https://example/session-42")
    assert not claim_mod.resumes_own_claim("", claim)


def test_resumes_own_claim_is_false_with_no_claim_at_all():
    assert not claim_mod.resumes_own_claim("https://example/session-42", None)
