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


MINE = "https://example.invalid/session_MINE"
THEIRS = "https://example.invalid/session_THEIRS"


def _body(session, extra=""):
    return f"{tracker.CLAIM_MARKER}{session}" + (f"\n\n{extra}" if extra else "")


def test_an_exact_session_resumes_its_own_claim():
    assert claim_mod.resumes_own_claim(MINE, {"body": _body(MINE)}) is True


def test_a_prefix_of_another_session_does_not_resume_it():
    """The reproduction from #24: containment matched a shared URL prefix."""
    prefix = "https://example.invalid/session_"
    assert claim_mod.resumes_own_claim(prefix, {"body": _body(THEIRS)}) is False


def test_a_longer_value_containing_the_claim_does_not_resume_it():
    assert claim_mod.resumes_own_claim(MINE + "-extra", {"body": _body(MINE)}) is False


def test_an_empty_session_never_resumes():
    assert claim_mod.resumes_own_claim("", {"body": _body(MINE)}) is False


def test_a_body_without_the_marker_never_resumes():
    assert claim_mod.resumes_own_claim(MINE, {"body": f"discussion of {MINE}"}) is False


def test_a_takeover_note_below_the_marker_line_is_ignored():
    """--take appends prose that can legitimately quote another session."""
    body = _body(MINE, extra=f"Takes over the claim posted by {THEIRS}, which expired.")
    assert claim_mod.resumes_own_claim(MINE, {"body": body}) is True
    assert claim_mod.resumes_own_claim(THEIRS, {"body": body}) is False


# --- select_next: which issue gets claimed, without a network ----------------

CANDIDATES = [(8, "eight"), (15, "fifteen"), (31, "thirty-one")]


def _judge(verdicts):
    """A stand-in for the claim-comment lookup, recording what it was asked."""
    asked = []

    def judge(number):
        asked.append(number)
        return verdicts.get(number, "free")

    return judge, asked


def test_the_lowest_numbered_free_issue_wins():
    judge, _ = _judge({})
    picked, skipped = claim_mod.select_next(CANDIDATES, judge)
    assert picked == (8, "eight")
    assert skipped == []


def test_a_live_claim_is_passed_over():
    judge, _ = _judge({8: "live"})
    picked, skipped = claim_mod.select_next(CANDIDATES, judge)
    assert picked == (15, "fifteen")
    assert skipped == [(8, "live")]


def test_a_stale_claim_is_passed_over_without_take():
    """--next must not choose WHICH claim to steal. `claim <n> --take` is a
    human naming an issue they looked at; this is not that act."""
    judge, _ = _judge({8: "stale"})
    picked, skipped = claim_mod.select_next(CANDIDATES, judge, take=False)
    assert picked == (15, "fifteen")
    assert skipped == [(8, "stale")]


def test_a_stale_claim_IS_taken_with_take():
    judge, _ = _judge({8: "stale"})
    picked, _ = claim_mod.select_next(CANDIDATES, judge, take=True)
    assert picked == (8, "eight")


def test_take_still_does_not_touch_a_live_claim():
    judge, _ = _judge({8: "live"})
    picked, _ = claim_mod.select_next(CANDIDATES, judge, take=True)
    assert picked == (15, "fifteen")


def test_nothing_claimable_reports_every_reason_rather_than_a_bare_none():
    judge, _ = _judge({8: "live", 15: "stale", 31: "live"})
    picked, skipped = claim_mod.select_next(CANDIDATES, judge)
    assert picked is None
    assert skipped == [(8, "live"), (15, "stale"), (31, "live")]


def test_judging_stops_at_the_first_hit():
    """Each judgement is an API call, so the walk must be lazy -- the common
    case is one call, not one per open issue."""
    judge, asked = _judge({})
    claim_mod.select_next(CANDIDATES, judge)
    assert asked == [8]


def test_no_candidates_is_none_not_an_error():
    judge, _ = _judge({})
    assert claim_mod.select_next([], judge) == (None, [])


def test_a_verdict_that_is_neither_free_nor_stale_is_skipped_even_with_take():
    """`--next` also meets an issue whose branch exists with no claim comment.
    `claim <n>` refuses outright there, deliberately; under `--next` that would
    abort the whole walk on the first such issue instead of moving past it."""
    judge, _ = _judge({8: "branch"})
    picked, skipped = claim_mod.select_next(CANDIDATES, judge, take=True)
    assert picked == (15, "fifteen")
    assert skipped == [(8, "branch")]


def test_neither_an_issue_nor_next_is_refused():
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        claim_mod.main([])


def test_both_an_issue_and_next_is_refused():
    """Silently preferring one would make the other argument a lie."""
    with pytest.raises(tracker.TrackerError, match="never both and never neither"):
        claim_mod.main(["19", "--next"])


def test_milestone_without_next_is_refused_rather_than_ignored():
    with pytest.raises(tracker.TrackerError, match="--milestone narrows"):
        claim_mod.main(["19", "--milestone", "Backlog"])
