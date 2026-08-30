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


def _claim(hours_ago):
    when = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"created_at": when, "body": tracker.CLAIM_MARKER + "https://example/s"}


def test_a_stale_claim_on_an_inactive_branch_reads_stale(tmp_path):
    repo = _repo(tmp_path)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), "feat/19-x", "main", NOW, cwd=str(repo))
    assert verdict == "stale"
    assert "EXPIRED" in lines[0] and "INACTIVE" in lines[1]


def test_a_stale_claim_on_an_ACTIVE_branch_reads_live(tmp_path):
    repo = _repo(tmp_path, commits_on_work=2)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(25), "feat/19-x", "main", NOW, cwd=str(repo))
    assert verdict == "live"
    assert "EXPIRED" in lines[0], "the age half held independently"
    assert "ACTIVE" in lines[1]


def test_a_fresh_claim_on_an_inactive_branch_reads_live(tmp_path):
    repo = _repo(tmp_path)
    verdict, lines = claim_mod.describe_claim(
        CFG, _claim(1), "feat/19-x", "main", NOW, cwd=str(repo))
    assert verdict == "live"
    assert "LIVE" in lines[0] and "INACTIVE" in lines[1]


def test_a_missing_branch_counts_as_no_activity(tmp_path):
    """A claim comment with no branch expires cleanly -- the safe failure."""
    repo = _repo(tmp_path)
    verdict, _ = claim_mod.describe_claim(
        CFG, _claim(25), "feat/19-nonexistent", "main", NOW, cwd=str(repo))
    assert verdict == "stale"


def test_a_missing_expiry_key_refuses_instead_of_crashing(tmp_path):
    """claim_expiry_hours is not in REQUIRED_KEYS, so a config can lack it."""
    repo = _repo(tmp_path)
    cfg = {k: v for k, v in CFG.items() if k != "claim_expiry_hours"}
    with pytest.raises(tracker.TrackerError, match="claim_expiry_hours"):
        claim_mod.describe_claim(cfg, _claim(25), "feat/19-x", "main", NOW,
                                 cwd=str(repo))


def test_there_is_no_flag_for_adopting_an_unclaimed_branch():
    """The refusal is deliberate, so an --adopt style escape must not parse.

    SystemExit comes from argparse, before any network call -- which also pins
    that parsing happens before the config load.
    """
    with pytest.raises(SystemExit):
        claim_mod.main(["19", "--adopt"])
