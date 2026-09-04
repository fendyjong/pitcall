"""The mechanism behind the issue verbs, exercised without a network."""

import os
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


def test_issue_from_branch_is_branch_names_inverse():
    cfg = {"branch_prefix": "feat/"}
    assert tracker.issue_from_branch(cfg, tracker.branch_name(cfg, 19, "Claim and file")) == "19"
    assert tracker.issue_from_branch({"branch_prefix": "wdd/"}, "wdd/1143-consent-pair") == "1143"


def test_issue_from_branch_fails_soft_rather_than_raising():
    """`branch_prefix` is required only for `claim`, so a plan branch cut by hand
    legitimately carries no number. Ship must still ship: every miss is None, and the
    caller states the omission. Raising here would refuse to open a PR over a naming
    convention the project never promised to follow."""
    # No prefix configured at all -- the key is optional outside `claim`.
    assert tracker.issue_from_branch({}, "feat/19-x") is None
    # Configured, but this branch does not use it.
    assert tracker.issue_from_branch({"branch_prefix": "feat/"}, "hotfix/19-x") is None
    # Prefix matches, remainder is not a number.
    assert tracker.issue_from_branch({"branch_prefix": "feat/"}, "feat/rename-the-thing") is None
    # Digits, but not the `<digits>-` shape -- `feat/43x-...` is not issue 43.
    assert tracker.issue_from_branch({"branch_prefix": "feat/"}, "feat/43x-thing") is None
    # Digits with nothing after them: no slug, so not the convention either.
    assert tracker.issue_from_branch({"branch_prefix": "feat/"}, "feat/43") is None


def test_issue_from_branch_does_not_match_a_number_deeper_in_the_name():
    """Anchored at the prefix, never searched for. `feat/rewrite-12-tables` contains a
    number and is not issue 12; emitting `Closes #12` there would close an unrelated
    issue in whatever repo `gh` happens to resolve to."""
    cfg = {"branch_prefix": "feat/"}
    assert tracker.issue_from_branch(cfg, "feat/rewrite-12-tables") is None
    assert tracker.issue_from_branch(cfg, "release/feat/19-x") is None


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


def test_commits_since_excludes_commits_before_since_and_includes_after(tmp_path):
    """`since` has to be a real filter, not decoration that every test passes
    a `datetime(2000, 1, 1)` and never actually exercises: a `since` set just
    after the real commit's own committer time must exclude it, and a
    `since` set just before it must include it."""
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
    committer_time = datetime.fromisoformat(subprocess.run(
        ("git", "-C", str(repo), "log", "-1", "--format=%cI"),
        check=True, capture_output=True, text=True,
    ).stdout.strip())
    just_before = committer_time - timedelta(seconds=1)
    just_after = committer_time + timedelta(seconds=1)
    assert tracker.commits_since("work", "main", just_before, cwd=str(repo)) == 1
    assert tracker.commits_since("work", "main", just_after, cwd=str(repo)) == 0


def test_commits_since_filters_on_committer_date_not_author_date(tmp_path):
    """The docstring says committer date, never author date, because a
    rebase preserves the author date and rewrites the committer date. Proven
    directly: a commit with an ancient AUTHOR date but today's COMMITTER date
    (GIT_AUTHOR_DATE overridden, GIT_COMMITTER_DATE left alone) must still
    count as recent -- `%aI` in place of `%cI` would report 0 here."""
    repo = tmp_path / "r"
    repo.mkdir()
    run = lambda *a, **kw: subprocess.run(("git", "-C", str(repo), *a), check=True,
                                          capture_output=True, **kw)
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
    old_author_env = {**os.environ, "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00"}
    run("commit", "-qm", "old author date, real committer date", env=old_author_env)
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert tracker.commits_since("work", "main", since, cwd=str(repo)) == 1, (
        "since=2020 is after the author date (2000) but before the real, "
        "unset committer date (now) -- %aI would report 0 here, %cI reports 1"
    )


def _branch_repo(tmp_path, *branches):
    """A repo with one commit on `main` and a branch per name given."""
    root = tmp_path / "r"
    root.mkdir()
    def g(*a):
        subprocess.run(("git", "-C", str(root), *a), check=True,
                       capture_output=True)
    subprocess.run(("git", "init", "-q", "-b", "main", str(root)), check=True,
                   capture_output=True)
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "t")
    (root / "a").write_text("1\n")
    g("add", "a")
    g("commit", "-qm", "base")
    for b in branches:
        g("branch", b)
    return root


CFG_PREFIX = {"branch_prefix": "feat/"}


def test_two_matching_branches_refuse_rather_than_guess(tmp_path):
    """The whole point: an alphabetical pick can choose the abandoned one."""
    repo = _branch_repo(tmp_path, "feat/19-aaa-abandoned", "feat/19-zzz-live")
    with pytest.raises(tracker.TrackerError, match="matches 2 branches"):
        tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(repo))


def test_the_refusal_names_every_candidate(tmp_path):
    repo = _branch_repo(tmp_path, "feat/19-aaa-abandoned", "feat/19-zzz-live")
    with pytest.raises(tracker.TrackerError) as exc:
        tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(repo))
    assert "feat/19-aaa-abandoned" in str(exc.value)
    assert "feat/19-zzz-live" in str(exc.value)


def test_one_branch_still_resolves(tmp_path):
    repo = _branch_repo(tmp_path, "feat/19-only")
    assert tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(repo)) == "feat/19-only"


def test_no_branch_is_none_not_an_error(tmp_path):
    repo = _branch_repo(tmp_path)
    assert tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(repo)) is None


def test_a_branch_and_its_remote_counterpart_are_one_branch(tmp_path):
    """Pushed work must not read as ambiguous with itself.

    Built as a real clone so the remote-tracking ref is genuine rather than a
    hand-made ref: `remotes/origin/feat/19-x` alongside local `feat/19-x`.
    """
    origin = _branch_repo(tmp_path, "feat/19-x")
    clone = tmp_path / "clone"
    subprocess.run(("git", "clone", "-q", f"file://{origin}", str(clone)),
                   check=True, capture_output=True)
    subprocess.run(("git", "-C", str(clone), "checkout", "-q", "feat/19-x"),
                   check=True, capture_output=True)
    assert tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(clone)) == "feat/19-x"


def test_a_remote_only_branch_still_resolves(tmp_path):
    """#19's origin-only fix must not regress: local-only listing saw nothing."""
    origin = _branch_repo(tmp_path, "feat/19-remote-only")
    clone = tmp_path / "clone2"
    subprocess.run(("git", "clone", "-q", f"file://{origin}", str(clone)),
                   check=True, capture_output=True)
    got = tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(clone))
    assert got is not None and got.endswith("feat/19-remote-only")


def test_a_branch_checked_out_in_a_linked_worktree_still_resolves_clean(tmp_path):
    """`git branch -a --list` marks a branch checked out in ANOTHER linked
    worktree with `+`, not `*` -- and that is the normal state here, since
    every WDD task gets its own worktree. `checkout`ing the branch in the
    SAME clone (as `test_a_branch_and_its_remote_counterpart_are_one_branch`
    does) only ever exercises `*`, so it can't catch a `+` left unstripped.

    Unstripped, `+ feat/19-x` either reads as a second, ambiguous branch next
    to its own `remotes/origin/feat/19-x` counterpart, or -- unambiguous --
    comes back as `resolve_branch`'s answer with a `+` still glued to the
    front, which then blows up whatever git command consumes it next.
    """
    repo = _branch_repo(tmp_path, "feat/19-x")
    linked = tmp_path / "linked"
    subprocess.run(("git", "-C", str(repo), "worktree", "add", "-q",
                    str(linked), "feat/19-x"), check=True, capture_output=True)
    # `repo` itself is still on `main` -- `feat/19-x` is checked out only in
    # `linked`, so listing from `repo` sees the `+` marker, never `*`.
    got = tracker.resolve_branch(CFG_PREFIX, 19, cwd=str(repo))
    assert got == "feat/19-x"


# --- select_candidates: the ordering decision, without a network -------------

def _issue(number, title="t", milestone=None, pull_request=False):
    d = {"number": number, "title": title,
         "milestone": {"title": milestone} if milestone else None}
    if pull_request:
        d["pull_request"] = {"url": "https://example.invalid/pr"}
    return d


def test_candidates_are_ordered_lowest_number_first():
    """FIFO is the whole point: the oldest issue has to come out first."""
    got = tracker.select_candidates([_issue(31), _issue(8), _issue(15)])
    assert [n for n, _ in got] == [8, 15, 31]


def test_pull_requests_are_excluded():
    """`repos/:r/issues` returns PRs too -- claiming one would be nonsense."""
    got = tracker.select_candidates([_issue(8), _issue(9, pull_request=True)])
    assert [n for n, _ in got] == [8]


def test_a_milestone_filter_keeps_only_that_milestone():
    got = tracker.select_candidates(
        [_issue(8, milestone="Backlog"), _issue(9, milestone="v2"), _issue(10)],
        milestone="Backlog")
    assert [n for n, _ in got] == [8]


def test_no_milestone_filter_keeps_issues_that_have_no_milestone():
    """A filter is opt-in; without one, an unmilestoned issue is a candidate.

    Ten of this repo's own open issues carry no milestone, and they are the
    oldest -- excluding them by default would starve exactly what FIFO protects.
    """
    got = tracker.select_candidates([_issue(8), _issue(9, milestone="Backlog")])
    assert [n for n, _ in got] == [8, 9]


def test_the_title_travels_with_the_number():
    got = tracker.select_candidates([_issue(8, title="a real title")])
    assert got == [(8, "a real title")]


def test_no_candidates_is_an_empty_list_not_an_error():
    assert tracker.select_candidates([]) == []
