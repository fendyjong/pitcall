"""Phase 3 — taking an integrated plan branch to a merged PR, then cleaning up.

The cases that matter are the ones where the ledger and git disagree. The
ledger is a controller's own account of what it did; git is what actually
happened. Every check here exists because trusting the first alone has a
failure mode nothing downstream would notice.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent
WDD = SKILL / "scripts" / "wdd"
SLUG = "2026-01-01-demo"
PLAN = f"docs/superpowers/plans/{SLUG}.md"

#: The four keys `load_config` requires of every project, plus the ones this
#: script reads. `default_branch` matches the fixture's remote branch;
#: `worktree_dir` matches the literal `.worktrees` the `run` fixture below
#: builds its own task worktrees under directly (bypassing `wave-worktree`),
#: so the two agree without either one driving the other.
CONFIG = {
    "bringup": None,
    "validate": "true",
    "default_branch": "master",
    "required_check": "ci",
    "worktree_dir": ".worktrees",
}


def git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def commit(cwd, msg):
    git("add", "-A", cwd=cwd)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg, cwd=cwd)


def commit_path(cwd, rel, msg):
    """Commit ONE path. Not `add -A`, and the difference is not stylistic.

    These fixtures nest a worktree inside a checkout, so `add -A` in the outer
    one stages the inner worktree as a GITLINK — after which
    `rev-parse --show-superproject-working-tree` answers from inside the
    worktree, `worktree_root()` climbs out to the outer checkout, and the
    config is read from a directory that has none. The test then fails on a
    missing config while claiming to be about something else entirely.
    """
    git("add", "--", rel, cwd=cwd)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg, cwd=cwd)


def finish(cwd, *args, env=None):
    """Run the real script from its real place in the plugin.

    Not a copy into the fixture repo: `wdd` resolves its siblings
    through `$HERE`, and `project-config` resolves the config loader four
    levels up from itself. A copy flattens that layout, so the copy would pass
    while the shipped arrangement failed — the one difference a test of a
    script's own path resolution must not introduce.

    `env` is how the `merge` tests put a stubbed `gh` on PATH. Passing None
    inherits this process's environment, which is what every other test wants.
    """
    return subprocess.run(
        ["bash", str(WDD), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


def write_config(checkout, **overrides):
    (checkout / "pitcall.config.json").write_text(json.dumps({**CONFIG, **overrides}))


def write_ledger(plan_wt, waves_dispatched, waves_integrated, plan=PLAN, extra=""):
    ws = plan_wt / ".pitcall" / "wdd" / SLUG
    ws.mkdir(parents=True, exist_ok=True)
    lines = [f"# WDD ledger — plan: {plan}"]
    for w in waves_dispatched:
        lines.append(f"Wave {w}: dispatched (BASE abc1234, T1 @ {SLUG}-t1 [cheapest/haiku])")
    for w in waves_integrated:
        lines.append(f"Wave {w}: integrated (merge def5678, pytest 1 passed, worktrees removed)")
    if extra:
        lines.append(extra)
    (ws / "progress.md").write_text("\n".join(lines) + "\n")
    return ws


@pytest.fixture
def run(tmp_path):
    """A main checkout, a plan worktree, and two task branches merged into it."""
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "master", str(origin), cwd=tmp_path)

    main = tmp_path / "main"
    git("clone", "-q", str(origin), str(main), cwd=tmp_path)
    (main / "seed.txt").write_text("seed\n")
    commit(main, "init")
    git("push", "-q", "origin", "master", cwd=main)

    plan_wt = main / ".worktrees" / "plan"
    git("worktree", "add", "-q", str(plan_wt), "-b", "planbranch", cwd=main)

    # wdd-workspace resolves the workspace from the plan file and refuses if it
    # is absent, so the fixture needs a real one on the plan branch.
    plan_file = plan_wt / PLAN
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text("# Demo Plan\n")
    # Committed, and on the plan branch: the config is read from the checkout
    # the work is happening in, so each branch carries its own.
    write_config(plan_wt)
    commit(plan_wt, "add plan and config")

    # Two task branches, each merged into the plan branch — the state Phase 3
    # is entitled to assume when every wave reported integrated.
    for n in (1, 2):
        b = f"{SLUG}-t{n}"
        git("branch", b, "planbranch", cwd=main)
        wt = plan_wt / ".worktrees" / b
        git("worktree", "add", "-q", str(wt), b, cwd=plan_wt)
        (wt / f"t{n}.txt").write_text(f"task {n}\n")
        commit(wt, f"task {n}")
        git("merge", "-q", "--no-edit", b, cwd=plan_wt)

    write_ledger(plan_wt, [1], [1])
    return {"main": main, "plan_wt": plan_wt, "origin": origin}


def test_refuses_to_run_in_the_main_checkout(run):
    """The guard that makes the shared-checkout hazard unreachable, not forbidden."""
    r = finish(run["main"], "check", PLAN)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "refusing to run in the main checkout" in r.stderr


def test_check_passes_when_every_wave_integrated_and_every_branch_merged(run):
    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verification OK" in r.stdout


def test_a_dispatched_wave_with_no_integrated_line_blocks_the_finish(run):
    write_ledger(run["plan_wt"], [1, 2], [1])
    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "wave 2 was dispatched but never integrated" in r.stderr


def test_a_task_branch_not_actually_merged_is_caught_despite_a_clean_ledger(run):
    """The ledger says integrated; git says otherwise. Nothing else checks this.

    An integrator that died after writing its line leaves exactly this state.
    """
    b = f"{SLUG}-t3"
    wt = run["plan_wt"] / ".worktrees" / b
    git("worktree", "add", "-q", str(wt), "-b", b, cwd=run["plan_wt"])
    (wt / "t3.txt").write_text("never merged\n")
    commit(wt, "task 3 — integrator died before merging")

    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"{b} is NOT merged" in r.stderr


def test_a_ledger_naming_another_plan_is_refused(run):
    write_ledger(run["plan_wt"], [1], [1], plan="docs/superpowers/plans/someone-elses.md")
    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "another run's" in r.stderr


def test_a_blocked_entry_blocks_the_finish(run):
    write_ledger(run["plan_wt"], [1], [1], extra="Task 2: BLOCKED (fix cap hit)")
    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "BLOCKED" in r.stderr


def test_cleanup_refuses_before_the_branch_reaches_origin_master(run):
    """Deleting a task branch whose work has not landed destroys the only copy."""
    r = finish(run["plan_wt"], "cleanup", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not merged into origin/" in r.stderr
    assert git("branch", "--list", f"{SLUG}-t1", cwd=run["main"]) != ""


def test_cleanup_deletes_task_branches_and_workspace_once_merged(run):
    git("push", "-q", "origin", "planbranch:master", cwd=run["plan_wt"])
    ws = run["plan_wt"] / ".pitcall" / "wdd" / SLUG
    assert ws.exists()

    r = finish(run["plan_wt"], "cleanup", PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git("branch", "--list", f"{SLUG}-t1", cwd=run["main"]) == ""
    assert git("branch", "--list", f"{SLUG}-t2", cwd=run["main"]) == ""
    assert not ws.exists()
    # The plan worktree is where we are standing; it cannot remove itself.
    assert "Plan worktree not removed" in r.stdout


def test_cleanup_uses_the_config_default_branch_not_a_stale_origin_head(run):
    """`origin/HEAD` is a clone-time cache, and this is what trusting it costs.

    The shape reproduced here is a remote that renamed its default branch:
    `origin/HEAD` still names the old one — `fetch --prune` does not update it
    — while the project's real default has moved. So `origin/master` exists,
    is live, and is NOT where this work landed.

    `cleanup` gates branch DELETION on `--is-ancestor HEAD origin/$BASE`.
    Deriving BASE from `origin/HEAD` here would check against `origin/master`,
    find HEAD is not an ancestor, and refuse — the benign direction of that
    bug. The malign direction, deleting task branches whose work never reached
    the real default, is the same wrong answer with the ancestry the other way
    round, and no test can tell them apart afterwards: both come from asking
    the wrong ref. So this asserts the base is taken from the config, which is
    the property that rules out both.
    """
    write_config(run["plan_wt"], default_branch="trunk")
    commit(run["plan_wt"], "the project's default branch is trunk")
    # The work lands on trunk, the real default. master stays behind, and
    # origin/HEAD still points at it.
    git("push", "-q", "origin", "planbranch:trunk", cwd=run["plan_wt"])
    git("remote", "set-head", "origin", "master", cwd=run["main"])
    assert git("symbolic-ref", "--short", "refs/remotes/origin/HEAD",
               cwd=run["main"]) == "origin/master"

    r = finish(run["plan_wt"], "cleanup", PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git("branch", "--list", f"{SLUG}-t1", cwd=run["main"]) == ""
    assert git("branch", "--list", f"{SLUG}-t2", cwd=run["main"]) == ""


def test_cleanup_refuses_when_the_config_cannot_answer(run):
    """No answer is a refusal, never a default.

    A guessed base either fails loudly on the fetch or — the case that matters
    — names a real branch nobody meant, and then decides whether task branches
    are deleted. Every task branch is the only copy of its work.
    """
    git("push", "-q", "origin", "planbranch:master", cwd=run["plan_wt"])
    (run["plan_wt"] / "pitcall.config.json").unlink()

    r = finish(run["plan_wt"], "cleanup", PLAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pitcall.config.json" in r.stderr          # project-config's own diagnosis
    assert "Refusing rather than guessing" in r.stderr  # and why we stop here
    # Nothing was deleted on the way to refusing.
    assert git("branch", "--list", f"{SLUG}-t1", cwd=run["main"]) != ""


def test_cleanup_removes_a_worktree_under_a_configured_worktree_dir_other_than_the_default(run):
    """`cleanup` must resolve `worktree_dir` from the SAME config `wave-worktree`
    used to create it -- not assume `.worktrees`.

    Every other fixture in this file, including `run` itself, builds its task
    worktrees directly under the literal `.worktrees`, which also happens to be
    `worktree_dir`'s value in `CONFIG`. A suite that never configures anything
    else cannot tell a `cleanup` that correctly reads `worktree_dir` from a
    `cleanup` that still has `.worktrees` hardcoded: both give the same answer
    for the same fixture. This test is the one that can, by configuring a
    DIFFERENT directory and creating the worktree through the real
    `wave-worktree` script -- the same script `cleanup` has to agree with.

    Without the fix, `cleanup` looks for the worktree under the old literal,
    finds nothing there (`[ -d "$d" ]` is false), skips the removal silently,
    and deletes the branch anyway -- the worktree leaks with nothing left
    pointing at it.
    """
    WAVE_WORKTREE = SKILL / "scripts" / "wave-worktree"

    write_config(run["plan_wt"], worktree_dir="wt")
    (run["plan_wt"] / ".gitignore").write_text("wt/\n")
    commit(run["plan_wt"], "worktree_dir is wt, not .worktrees")

    b = f"{SLUG}-t3"
    head = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    created = subprocess.run(
        [str(WAVE_WORKTREE), "create", SLUG, "3", head],
        cwd=run["plan_wt"], capture_output=True, text=True,
    )
    assert created.returncode == 0, created.stderr
    wt = Path(created.stdout.strip())
    assert wt == run["plan_wt"] / "wt" / b, "sanity: the worktree really landed under the configured dir"

    (wt / "t3.txt").write_text("task 3\n")
    commit(wt, "task 3")
    git("merge", "-q", "--no-edit", b, cwd=run["plan_wt"])
    git("push", "-q", "origin", "planbranch:master", cwd=run["plan_wt"])

    assert wt.exists(), "sanity: the worktree exists before cleanup runs"

    r = finish(run["plan_wt"], "cleanup", PLAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not wt.exists(), "the worktree under the configured worktree_dir was not removed"
    assert git("branch", "--list", b, cwd=run["main"]) == ""
    assert f"worktree removed: {b}" in r.stdout


def test_check_counts_every_task_branch(run):
    """The printed count must equal the branches actually verified.

    `printf '%s' "$list" | wc -l` counts NEWLINES, so an unterminated list reports
    n-1 — a real run showed six branches as five. The per-branch ancestry loop was
    correct throughout, which is what made it survive: only the number lied, and a
    verification tool that miscounts by one is one nobody trusts.
    """
    # The fixture ships two task branches; add a third so an off-by-one cannot
    # coincide with a plausible-looking number.
    b = f"{SLUG}-t3"
    git("branch", b, "planbranch", cwd=run["main"])

    r = finish(run["plan_wt"], "check", PLAN)
    assert r.returncode == 0, r.stdout + r.stderr

    listed = git("for-each-ref", "--format=%(refname:short)",
                 f"refs/heads/{SLUG}-t*", cwd=run["main"]).splitlines()
    assert len(listed) == 3
    assert "task branches: 3" in r.stdout, r.stdout


# ---------------------------------------------------------------------------
# `merge` — landing the PR on evidence rather than on attention.
#
# Every test below stubs `gh` on PATH and asserts on the STUB'S CALL LOG, not
# on the exit status alone. "It refused" and "it refused for the right reason"
# are different claims, and a test that cannot tell them apart passes when the
# code refuses for a reason nobody wanted -- a missing binary, a typo in a
# subcommand, an unrelated guard firing first.
#
# The negative cases here were written RED against a deliberately over-eager
# implementation -- one that merged whenever the check was green and never
# looked for a receipt -- because a plain RED run proves nothing about a test
# asserting that something does NOT happen: it passes before the feature exists
# and after, for the same reason.
# ---------------------------------------------------------------------------

GH_STUB = r"""#!/usr/bin/env bash
# A stub `gh`: records every call, answers from canned files, never talks to
# GitHub. `pr merge` really pushes the plan branch to the fixture's origin, so
# the fast-forward that follows has something to do and can be asserted on
# rather than assumed.
printf '%s\n' "$*" >> "$GH_LOG"
case "$*" in
  "pr view "*"--json number"*)
    cat "$GH_DIR/pr.json"
    ;;
  "pr merge --help"*)
    # The capability probe. A gh too old to pin the head says so here, and
    # `no_match_head_flag` is how a test builds one.
    echo "Merge a pull request"
    [ -f "$GH_DIR/no_match_head_flag" ] || echo "      --match-head-commit SHA   Commit SHA that pull request head must match"
    # A future gh with longer help. The flag comes FIRST and the padding after,
    # which is what makes an early-exiting reader SIGPIPE the writer.
    # An `if`, not `[ ... ] && ...`: as the LAST command in a case branch the
    # latter's failure becomes the stub's exit status, and a `gh` that exits
    # non-zero on --help is a different test than this one.
    if [ -f "$GH_DIR/big_help" ]; then
      head -c 70000 /dev/zero | tr '\0' 'x'
      echo
    fi
    ;;
  "pr view "*"--json mergeStateStatus"*)
    # The re-read. GitHub computes mergeStateStatus lazily, so the first view
    # of a PR is the request that TRIGGERS the computation and is therefore
    # likeliest to answer UNKNOWN.
    if [ -f "$GH_DIR/mergestate_reread.json" ]; then
      cat "$GH_DIR/mergestate_reread.json"
    else
      cat "$GH_DIR/pr.json"
    fi
    ;;
  "pr view "*"--json statusCheckRollup"*)
    # Per-call answers, so a test can make a check finish mid-wait: the Nth
    # poll reads checks-N.json when it exists and the default otherwise.
    n=$(cat "$GH_DIR/rollup_n" 2>/dev/null || echo 0)
    n=$((n + 1))
    printf '%s' "$n" > "$GH_DIR/rollup_n"
    # A push landing mid-wait: from poll N on, the PR's head is a new commit.
    # The rollup answers about THAT commit, which is the trap -- a green
    # signal describing a commit no receipt covers.
    [ -f "$GH_DIR/head-at-$n" ] && cp "$GH_DIR/head-at-$n" "$GH_DIR/current_head"
    if [ -f "$GH_DIR/checks-$n.json" ]; then
      cat "$GH_DIR/checks-$n.json"
    else
      cat "$GH_DIR/checks.json"
    fi
    ;;
  "pr merge"*)
    if [ -f "$GH_DIR/merge_refuses" ]; then
      echo "gh: pull request is not mergeable" >&2
      exit 1
    fi
    # GitHub's own behaviour, and the asymmetry is the point: WITH
    # --match-head-commit a moved head is refused server-side; WITHOUT it the
    # merge takes whatever the head is now. An unpinned merge here really does
    # land the unvalidated commit, which is what makes the guard's test honest.
    want=""; prev=""
    for a in "$@"; do
      [ "$prev" = "--match-head-commit" ] && want="$a"
      prev="$a"
    done
    now="$(cat "$GH_DIR/current_head" 2>/dev/null || echo "")"
    if [ -n "$want" ] && [ -n "$now" ] && [ "$want" != "$now" ]; then
      echo "Pull request Head branch was modified. Review and try the merge again." >&2
      exit 1
    fi
    git -C "$GH_PLAN_WT" push -q origin "planbranch:$GH_BASE"
    ;;
  *)
    echo "gh stub: unexpected call: $*" >&2
    exit 1
    ;;
esac
"""


def rollup(*checks, merge_state="CLEAN"):
    """A poll payload in the shape `gh pr view --json` returns.

    Each check is a (name, status, conclusion) triple. Real JSON rather than a
    canned verdict word: the classification of QUEUED / COMPLETED+SUCCESS /
    COMPLETED+FAILURE is our logic, so a test that fed it a pre-digested answer
    would assert nothing about the part that can be wrong.

    `merge_state` rides along because the poll reads it from the same call --
    the base can move under a branch during a thirty-minute wait, and the head
    guard cannot see that: the head has not moved.
    """
    return json.dumps({
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": n, "status": s, "conclusion": c}
            for n, s, c in checks
        ],
        "mergeStateStatus": merge_state,
    })


def receipt_body(sha, plan_wt):
    return {
        "sha": sha,
        "branch": "planbranch",
        "session": "session_01test",
        "worktree": str(plan_wt),
        "validate_command": "true",
        "validated_at": 1756400000.0,
    }


def write_receipt(run, sha, raw=None):
    """Put a lane receipt where `lane run` would have put it.

    `shared_root()/.pitcall/receipts/<sha>.json` — the SHARED root, so every
    worktree of the project reads one set. For this fixture that is the main
    checkout, not the plan worktree the merge runs from, which is exactly the
    arrangement that would silently pass if the merge step re-derived the path
    from its own worktree instead.
    """
    d = run["main"] / ".pitcall" / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha}.json"
    p.write_text(raw if raw is not None
                 else json.dumps(receipt_body(sha, run["plan_wt"])))
    return p


@pytest.fixture
def gh(tmp_path, run):
    """A stubbed `gh` on PATH plus the environment `merge` reads."""
    d = tmp_path / "gh"
    d.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)

    head = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    (d / "pr.json").write_text(json.dumps(
        {"number": 7, "state": "OPEN", "headRefOid": head,
         "mergeStateStatus": "CLEAN", "baseRefName": "master"}))
    (d / "checks.json").write_text(rollup(("ci", "COMPLETED", "SUCCESS")))
    # What the PR's head is on the server right now. A test moves it to
    # simulate a push landing mid-wait.
    (d / "current_head").write_text(head)

    return {
        "dir": d,
        "head": head,
        "log": d / "calls.log",
        "env": {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "GH_DIR": str(d),
            "GH_LOG": str(d / "calls.log"),
            "GH_PLAN_WT": str(run["plan_wt"]),
            "GH_BASE": "master",
            # One poll, then the bound is spent. No test waits on wall time.
            "WDD_CHECK_TIMEOUT": "0",
            "WDD_CHECK_POLL": "0",
        },
    }


def set_pr_head(gh, sha, merge_state="CLEAN", base="master"):
    """Point the PR at `sha` — in BOTH places the stub reads.

    `pr.json` is what `gh pr view` answers; `current_head` is what the server
    would refuse a mismatched `--match-head-commit` against. Updating only the
    first models a state GitHub cannot be in, and the stub then refuses a merge
    the test meant to allow.
    """
    (gh["dir"] / "pr.json").write_text(json.dumps(
        {"number": 7, "state": "OPEN", "headRefOid": sha,
         "mergeStateStatus": merge_state, "baseRefName": base}))
    (gh["dir"] / "current_head").write_text(sha)


def calls(gh, prefix=""):
    log = Path(gh["log"])
    lines = log.read_text().splitlines() if log.exists() else []
    return [c for c in lines if c.startswith(prefix)]


def merges(gh):
    """Actual merge attempts.

    The capability probe shares the verb (`gh pr merge --help`), so a bare
    prefix match on "pr merge" counts it as a merge — which would quietly
    invert every assertion in this file that nothing was merged.
    """
    return [c for c in calls(gh, "pr merge") if "--help" not in c]


def test_merge_lands_the_pr_and_then_fast_forwards_the_main_checkout(run, gh):
    write_receipt(run, gh["head"])
    before = git("rev-parse", "HEAD", cwd=run["main"])

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr

    attempts = merges(gh)
    assert attempts, "the PR was never merged"
    # Pinned to the sha the receipt covers. Without this the merge takes
    # whatever the head is at merge time, which is a different commit whenever
    # anything was pushed during the wait.
    assert f"--match-head-commit {gh['head']}" in attempts[0], attempts
    # The merge is the first half; the checkout going level with it is the
    # other, and it is read back rather than inferred from the absence of an
    # error.
    after = git("rev-parse", "HEAD", cwd=run["main"])
    assert after != before
    assert after == git("rev-parse", "origin/master", cwd=run["main"])
    assert after == git("rev-parse", "planbranch", cwd=run["main"])


def test_merge_refuses_without_a_receipt_and_never_calls_gh_pr_merge(run, gh):
    """The gate, observed in the direction where it holds.

    Checks are GREEN here, so nothing but the missing receipt can be doing the
    refusing — and the check was never even polled, which is the ordering the
    step promises: refusing early costs nothing, refusing after a ten-minute
    wait means having waited for checks on a branch that could never merge.
    """
    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])

    assert r.returncode == 1, r.stdout + r.stderr
    assert "no lane receipt" in r.stderr
    assert merges(gh) == []
    assert calls(gh, "pr view") and not [
        c for c in calls(gh) if "statusCheckRollup" in c
    ], "the receipt must be checked BEFORE waiting on checks"


def test_merge_refuses_a_receipt_that_names_a_different_commit(run, gh):
    """The review-fix case: validation ran, and then another commit landed.

    A branch-scoped receipt would permit exactly this, which is why the receipt
    is filed under a sha. The earlier commit really was validated; the one the
    PR would merge was not, and approval does not carry forward.
    """
    validated = git("rev-parse", "HEAD~1", cwd=run["plan_wt"])
    assert validated != gh["head"]
    write_receipt(run, validated)

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no lane receipt" in r.stderr
    assert "other commit" in r.stderr, "say WHY there is a receipt directory but no match"
    assert merges(gh) == []


def test_a_corrupt_receipt_is_treated_as_absent(run, gh):
    """Unreadable must never widen into permitted.

    The file exists at the exact path, so a step that took existence alone as
    the verdict without ever opening it would merge here. Parsing is for the
    message; a body that will not parse is evidence of nothing.
    """
    write_receipt(run, gh["head"], raw="{this is not json")

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "will not parse" in r.stderr
    assert merges(gh) == []


def test_merge_reports_not_merged_when_the_check_is_still_running(run, gh):
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks.json").write_text(rollup(("ci", "IN_PROGRESS", None)))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NOT MERGED" in r.stderr
    assert "still running" in r.stderr
    assert merges(gh) == []
    assert git("rev-parse", "HEAD", cwd=run["main"]) == git(
        "rev-parse", "master", cwd=run["main"])


def test_merge_refuses_when_the_required_check_is_red(run, gh):
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks.json").write_text(rollup(("ci", "COMPLETED", "FAILURE")))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not green" in r.stderr.lower()
    assert merges(gh) == []


def test_merge_waits_for_a_check_that_finishes_mid_wait(run, gh):
    """The bound is a bound, not a single glance.

    A step that polled once and gave up would exit 3 here, and a step that
    never polled at all would merge on the first answer. Two pending answers
    then a green one distinguishes both.
    """
    write_receipt(run, gh["head"])
    pending = rollup(("ci", "QUEUED", None))
    (gh["dir"] / "checks-1.json").write_text(pending)
    (gh["dir"] / "checks-2.json").write_text(pending)
    env = {**gh["env"], "WDD_CHECK_TIMEOUT": "30"}

    r = finish(run["plan_wt"], "merge", PLAN, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len([c for c in calls(gh) if "statusCheckRollup" in c]) == 3
    assert merges(gh)


def test_merge_gates_on_the_check_named_by_the_config_not_a_hardcoded_one(run, gh):
    """A hardcoded check name is a gate on someone else's project.

    The rollup here is green on `ci` — the name a plugin would most plausibly
    have baked in — and red on the one this project actually configured. A
    merge is the wrong answer, and it is the answer a hardcoded name gives.
    """
    write_config(run["plan_wt"], required_check="gate-x")
    commit_path(run["plan_wt"], "pitcall.config.json",
                "this project's required check is gate-x")
    head = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    set_pr_head(gh, head)
    write_receipt(run, head)
    (gh["dir"] / "checks.json").write_text(
        rollup(("ci", "COMPLETED", "SUCCESS"), ("gate-x", "COMPLETED", "FAILURE")))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "gate-x" in r.stderr
    assert merges(gh) == []


def test_a_diverged_main_checkout_is_reported_and_not_repaired(run, gh):
    """The refusal is the interesting half of `--ff-only`.

    A local commit in a checkout nobody authors in IS the bug, so repairing it
    with a merge would mint one more commit and erase the evidence of the thing
    that was supposed to be impossible. The PR merge itself is not in question
    here — it was gated on a receipt and a green check and it stands; the sync
    is the half that failed, and the exit code has to say which.
    """
    (run["main"] / "stray.txt").write_text("committed in the shared checkout\n")
    commit_path(run["main"], "stray.txt", "a local commit nobody meant to make")
    diverged = git("rev-parse", "HEAD", cwd=run["main"])
    write_receipt(run, gh["head"])

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 4, r.stdout + r.stderr

    out = r.stdout + r.stderr
    assert "would NOT fast-forward" in out
    assert "a local commit nobody meant to make" in out, "name what diverged"
    # Merged, and then left alone: no repair, no second merge commit.
    assert merges(gh)
    assert git("rev-parse", "HEAD", cwd=run["main"]) == diverged


def test_a_failing_refresh_does_not_unwind_the_merge(run, gh):
    """Best-effort means the merge stands. It has already happened."""
    write_config(run["plan_wt"], refresh_commands=["exit 3"])
    commit_path(run["plan_wt"], "pitcall.config.json",
                "this project regenerates something after a pull")
    head = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    set_pr_head(gh, head)
    write_receipt(run, head)

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "refresh FAILED" in r.stdout
    assert git("rev-parse", "HEAD", cwd=run["main"]) == git(
        "rev-parse", "origin/master", cwd=run["main"])


# ---------------------------------------------------------------------------
# Fix round 1. Three windows the first version left open, each of which the
# ordinary way of working walks straight into.
# ---------------------------------------------------------------------------


def test_a_head_that_moves_during_the_wait_is_refused_not_merged(run, gh):
    """The gate, moved a few minutes later, is still the gate.

    The head sha is read once, and the wait that follows can run for half an
    hour. `checks are running, let me push the fix now` is not an exotic
    sequence, it is the normal one — and `statusCheckRollup` then reports
    GREEN about the new commit, so every signal the step reads agrees that
    merging is fine. Only the sha disagrees.

    Closed server-side with `--match-head-commit`, not by re-reading the head
    before merging: re-reading narrows the window, and a narrower race is the
    kind nobody reproduces afterwards.
    """
    write_receipt(run, gh["head"])
    # The fix is pushed from ELSEWHERE — another worktree, the web UI, a
    # teammate — so this checkout's HEAD does not move. That is load-bearing
    # rather than incidental: a fix committed *here* is caught earlier and more
    # cheaply by the local-HEAD guard, and if this test let local HEAD move it
    # would exercise that guard instead and stop saying anything at all about
    # the head pin. Kept on a ref so the commit stays reachable.
    (run["plan_wt"] / "fix.txt").write_text("a review fix, never validated\n")
    commit_path(run["plan_wt"], "fix.txt", "UNVALIDATED review fix")
    moved = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    git("branch", "pushed-from-elsewhere", moved, cwd=run["plan_wt"])
    git("reset", "-q", "--hard", "HEAD~1", cwd=run["plan_wt"])
    assert moved != gh["head"]
    assert git("rev-parse", "HEAD", cwd=run["plan_wt"]) == gh["head"], \
        "local HEAD must stay put, or this tests the wrong guard"

    # Pushed while the check was still running: pending, then the new head,
    # then green about it.
    (gh["dir"] / "checks-1.json").write_text(rollup(("ci", "IN_PROGRESS", None)))
    (gh["dir"] / "head-at-2").write_text(moved)
    env = {**gh["env"], "WDD_CHECK_TIMEOUT": "30"}

    r = finish(run["plan_wt"], "merge", PLAN, env=env)
    assert r.returncode != 0, r.stdout + r.stderr
    # Pinned to the commit the RECEIPT covers, not to the one now on the PR.
    assert f"--match-head-commit {gh['head']}" in merges(gh)[0]
    # And the unvalidated commit did not land.
    landed = git("log", "--format=%H", "origin/master", cwd=run["main"])
    assert moved not in landed, "an unvalidated commit reached the base branch"


def test_a_gh_that_cannot_pin_the_head_refuses_rather_than_merging_unguarded(run, gh):
    """A silent fallback to an unguarded merge is worse than refusing to run.

    The flag landed in gh 2.96. An older binary would otherwise merge exactly
    as before — with the guard silently absent, which is the failure state
    nobody can see from the outside.
    """
    write_receipt(run, gh["head"])
    (gh["dir"] / "no_match_head_flag").touch()

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "--match-head-commit" in r.stderr
    assert merges(gh) == []


def test_a_receipt_whose_body_names_another_commit_is_refused(run, gh):
    """Existence is the verdict — and a body that contradicts the path is not
    a second verdict, it is a reason to refuse.

    The check is one-directional and that is what keeps the contract intact: it
    can turn a pass into a refusal, never a refusal into a pass. What it costs
    a stray `cp` of one receipt onto another's name is a refusal instead of a
    merge, and the realistic actor is a frustrated session, not an attacker.
    """
    other = git("rev-parse", "HEAD~1", cwd=run["plan_wt"])
    body = receipt_body(other, run["plan_wt"])
    write_receipt(run, gh["head"], raw=json.dumps(body))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "names a different commit" in r.stderr
    assert merges(gh) == []


def test_a_receipt_symlinked_from_another_commits_receipt_is_refused(run, gh):
    """`-f` follows a symlink, so the path test alone reads this as present."""
    other = git("rev-parse", "HEAD~1", cwd=run["plan_wt"])
    real = write_receipt(run, other)
    link = real.parent / f"{gh['head']}.json"
    link.symlink_to(real.name)

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "names a different commit" in r.stderr
    assert merges(gh) == []


def test_a_pr_behind_its_base_is_refused_before_any_wait(run, gh):
    """Branch protection is not observable from here, so it is not relied on.

    Merging a branch whose base has moved produces a merge commit combining two
    states nothing validated — the migration-number hazard this skill's own
    documentation describes. Delegating that to `require branches to be up to
    date` is defensible right up until the project has not enabled it, and
    nothing here can tell.
    """
    write_receipt(run, gh["head"])
    set_pr_head(gh, gh["head"], merge_state="BEHIND")

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "behind" in r.stderr.lower()
    assert merges(gh) == []
    assert not [c for c in calls(gh) if "statusCheckRollup" in c], \
        "a branch that cannot merge should not be waited on"


def test_a_base_that_moves_during_the_wait_is_refused_not_merged(run, gh):
    """The head guard cannot see this one: the head never moved.

    A thirty-minute wait is long enough for the base to move under the branch,
    and the poll already reads `mergeStateStatus` from the same call — so the
    window costs one field to close and would otherwise stay open for the whole
    wait.
    """
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks-1.json").write_text(rollup(("ci", "IN_PROGRESS", None)))
    (gh["dir"] / "checks-2.json").write_text(
        rollup(("ci", "COMPLETED", "SUCCESS"), merge_state="BEHIND"))
    env = {**gh["env"], "WDD_CHECK_TIMEOUT": "30"}

    r = finish(run["plan_wt"], "merge", PLAN, env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "behind" in r.stderr.lower()
    assert merges(gh) == []


# ---------------------------------------------------------------------------
# Fix round 2. Three of these are about a value the script does not control
# steering the script, and one is about a guard that is quieter in production
# than it looks in a test.
# ---------------------------------------------------------------------------


def test_a_receipt_sha_made_of_newlines_refuses_diagnosably(run, gh):
    """"Non-empty by construction" was held by the VALUE, not the structure.

    `{"sha": "\n"}` printed a line that `$( )` then stripped to nothing, the
    caller's second `read` hit EOF, and `set -e` ended the run with an empty
    stderr. The refusal survived, so this was never a hole — but F2 exists
    precisely because the receipt body is untrustworthy, and this is that body
    steering control flow.
    """
    write_receipt(run, gh["head"], raw=json.dumps({"sha": "\n"}))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stderr.strip(), "refused with no diagnosis at all"
    assert "names a different commit" in r.stderr
    assert merges(gh) == []


def test_a_merge_state_of_whitespace_does_not_end_the_run_silently(run, gh):
    """The same shape reachable through `mergeStateStatus`.

    Uninterpretable is not BEHIND and not DIRTY, so the run proceeds — the head
    pin and the check still gate it. What must not happen is the run ending
    with nothing said.
    """
    write_receipt(run, gh["head"])
    set_pr_head(gh, gh["head"], merge_state="\n")

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert merges(gh)


def test_a_conflicted_pr_is_refused_immediately_not_after_the_bound(run, gh):
    """DIRTY was the common case and it was unguarded.

    Measured across 20 open PRs: 16 DIRTY to 4 BEHIND. Unguarded, a conflicting
    PR waits the full bound and then attempts a merge GitHub was always going
    to refuse — half an hour to arrive at an inaccurate message.
    """
    write_receipt(run, gh["head"])
    set_pr_head(gh, gh["head"], merge_state="DIRTY")

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "conflict" in r.stderr.lower()
    assert merges(gh) == []
    assert not [c for c in calls(gh) if "statusCheckRollup" in c], \
        "a PR that cannot merge should not be waited on"


def test_a_gh_whose_help_exceeds_the_pipe_buffer_probes_as_capable(run, gh):
    """`pipefail` + an early-exiting reader turns a CAPABLE gh into a refusal.

    The old probe piped `--help` into `grep -q`, which exits at the match and
    SIGPIPEs the writer once the output no longer fits the pipe buffer. Inert
    at today's 1916 bytes; a future gh with longer help would make the probe
    refuse on EVERY binary, reporting "your gh is too old" about one that is
    not. The flag is emitted before the padding, which is the ordering that
    makes an early exit possible.
    """
    write_receipt(run, gh["head"])
    (gh["dir"] / "big_help").touch()

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert merges(gh)


def test_an_unknown_merge_state_is_read_again_before_the_wait(run, gh):
    """The pre-wait guard reads the request that triggers the computation.

    Measured: one batch over 20 open PRs answered UNKNOWN 20/20, and the
    identical query moments later returned 16 DIRTY and 4 BEHIND. So the first
    read is the one least likely to know, and a guard that only ever reads it
    is frequently a no-op.
    """
    write_receipt(run, gh["head"])
    set_pr_head(gh, gh["head"], merge_state="UNKNOWN")
    (gh["dir"] / "mergestate_reread.json").write_text(
        json.dumps({"mergeStateStatus": "BEHIND"}))
    env = {**gh["env"], "WDD_MERGE_STATE_RETRY": "0"}

    r = finish(run["plan_wt"], "merge", PLAN, env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "behind" in r.stderr.lower()
    assert merges(gh) == []
    assert not [c for c in calls(gh) if "statusCheckRollup" in c]


# ---------------------------------------------------------------------------
# Final round. One fail-open, and three places the step reported something
# other than what it did.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("checks,rc,expect", [
    ((("ci", "COMPLETED", "SUCCESS"), ("ci", "COMPLETED", "FAILURE")), 1, "not green"),
    ((("ci", "COMPLETED", "FAILURE"), ("ci", "COMPLETED", "SUCCESS")), 1, "not green"),
    ((("ci", "COMPLETED", "SUCCESS"), ("ci", "IN_PROGRESS", None)), 3, "still running"),
], ids=["green-then-red", "red-then-green", "green-then-running"])
def test_a_duplicated_check_name_is_judged_by_its_worst_result(
        run, gh, checks, rc, expect):
    """A verdict that depended on ARRAY ORDER, and merged on the lucky one.

    The parser took the first entry whose name matched and stopped, so a
    repository where `required_check`'s name appears twice — an external app's
    commit status colliding with a workflow job, or two workflow files each
    with a job of that name — merged over a red or still-running check
    whenever the green one happened to be listed first.

    Red-first was always correct, which is exactly why this survives casual
    testing: the configuration that fails is a naming coincidence, not a
    mistake anyone would notice making. Both orders are asserted here for that
    reason — one of them passing proves nothing about the other.
    """
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks.json").write_text(rollup(*checks))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == rc, r.stdout + r.stderr
    assert expect in r.stderr.lower()
    assert merges(gh) == []


def test_a_local_head_ahead_of_the_pr_is_refused_naming_both(run, gh):
    """The verification above ran against a commit the merge will not land.

    Everything before this point — every wave integrated, every task branch an
    ancestor — is asserted against local `HEAD`. The merge lands `PR_SHA`. A
    review fix committed locally and not pushed makes those different commits,
    and the run would report MERGED, exit 0, and not contain the fix. Nothing
    unvalidated lands, because the receipt still covers what GitHub merged —
    but the operator is told the wrong thing, and `cleanup` refuses later for
    reasons that do not obviously connect back to here.
    """
    write_receipt(run, gh["head"])
    (run["plan_wt"] / "unpushed.txt").write_text("committed here, never pushed\n")
    commit_path(run["plan_wt"], "unpushed.txt", "a fix that never left this checkout")
    local = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    assert local != gh["head"]

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert local in out and gh["head"] in out, "name both, or the reader cannot act"
    assert merges(gh) == []
    assert not [c for c in calls(gh) if "statusCheckRollup" in c]


def test_a_pr_retargeted_to_another_base_is_refused(run, gh):
    """The base is read from the config; the PR carries its own.

    Retargeting happens on GitHub, where nothing in this checkout can see it.
    The merge would land in one branch while step 5 fast-forwarded the main
    checkout to another and printed `merged into <the config's base>` — a
    sentence that is simply false, about the branch a human is most likely to
    trust it on.
    """
    write_receipt(run, gh["head"])
    set_pr_head(gh, gh["head"], base="release-2")

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "release-2" in r.stderr and "master" in r.stderr
    assert merges(gh) == []


def test_every_script_named_in_the_skill_exists():
    """A `scripts/<name>` in the prose must resolve to a file that ships.

    Nothing else checks this. `tests/test_plugin_closure.py` catches a dangling
    `pitcall:<skill>` and a dangling `../` path, and a script reference is
    neither — so renaming a script and missing one mention in SKILL.md leaves a
    controller running a command that does not exist, with every gate green.
    That is not hypothetical: this script was named `wdd-finish` and its name is
    spelled seven times across the skill's markdown.

    **A reference carries its own anchor, and there are two.** A bare
    `scripts/<name>` is relative to this skill's directory, as the Phase 1
    validate step says. A `${CLAUDE_PLUGIN_ROOT}/scripts/<name>` is the plugin
    root — that is where `lane.py` lives, because the lane belongs to the plugin
    rather than to this skill. Resolving both against the skill would report a
    shipped script as missing, which reads as a broken citation and invites
    "fixing" a correct path; resolving both against the plugin root would let a
    renamed skill script pass. So each is checked where it actually claims to
    be, and a bare `scripts/lane.py` still fails — it names the wrong place.
    """
    import re

    plugin_root = SKILL.parent.parent

    referenced = set()
    for doc in sorted(SKILL.glob("*.md")):
        for anchor, name in re.findall(
            r"(\$\{CLAUDE_PLUGIN_ROOT\}/)?scripts/([a-z0-9][a-z0-9_-]*(?:\.py)?)",
            doc.read_text(),
        ):
            referenced.add((doc.name, name, plugin_root if anchor else SKILL))

    assert referenced, "no script references found - the scan did not run"
    missing = sorted(
        (doc, name) for doc, name, root in referenced
        if not (root / "scripts" / name).is_file()
    )
    assert not missing, f"named in the skill's prose but not shipped: {missing}"


def test_the_retired_script_name_is_gone():
    """`wdd-finish` was renamed to `wdd`; the old path must not come back.

    A stray copy left behind would be found first by anyone following an older
    document, and would keep working — which is exactly why nothing would
    report the duplication.
    """
    assert not (SKILL / "scripts" / "wdd-finish").exists()
