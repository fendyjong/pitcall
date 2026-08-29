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
WDD_FINISH = SKILL / "scripts" / "wdd-finish"
SLUG = "2026-01-01-demo"
PLAN = f"docs/superpowers/plans/{SLUG}.md"

#: The four keys `load_config` requires of every project, plus the one this
#: script reads. `default_branch` matches the fixture's remote branch.
CONFIG = {
    "bringup": None,
    "validate": "true",
    "default_branch": "master",
    "required_check": "ci",
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

    Not a copy into the fixture repo: `wdd-finish` resolves its siblings
    through `$HERE`, and `project-config` resolves the config loader four
    levels up from itself. A copy flattens that layout, so the copy would pass
    while the shipped arrangement failed — the one difference a test of a
    script's own path resolution must not introduce.

    `env` is how the `merge` tests put a stubbed `gh` on PATH. Passing None
    inherits this process's environment, which is what every other test wants.
    """
    return subprocess.run(
        ["bash", str(WDD_FINISH), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


def write_config(checkout, **overrides):
    (checkout / "pitcall.config.json").write_text(json.dumps({**CONFIG, **overrides}))


def write_ledger(plan_wt, waves_dispatched, waves_integrated, plan=PLAN, extra=""):
    ws = plan_wt / ".superpowers" / "wdd" / SLUG
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
    ws = run["plan_wt"] / ".superpowers" / "wdd" / SLUG
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
  "pr view "*"--json statusCheckRollup"*)
    # Per-call answers, so a test can make a check finish mid-wait: the Nth
    # poll reads checks-N.json when it exists and the default otherwise.
    n=$(cat "$GH_DIR/rollup_n" 2>/dev/null || echo 0)
    n=$((n + 1))
    printf '%s' "$n" > "$GH_DIR/rollup_n"
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
    git -C "$GH_PLAN_WT" push -q origin "planbranch:$GH_BASE"
    ;;
  *)
    echo "gh stub: unexpected call: $*" >&2
    exit 1
    ;;
esac
"""


def rollup(*checks):
    """A `statusCheckRollup` payload in the shape `gh pr view --json` returns.

    Each item is a (name, status, conclusion) triple. Real JSON rather than a
    canned verdict word: the classification of QUEUED / COMPLETED+SUCCESS /
    COMPLETED+FAILURE is our logic, so a test that fed it a pre-digested answer
    would assert nothing about the part that can be wrong.
    """
    return json.dumps({
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": n, "status": s, "conclusion": c}
            for n, s, c in checks
        ]
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
    (d / "pr.json").write_text(
        json.dumps({"number": 7, "state": "OPEN", "headRefOid": head}))
    (d / "checks.json").write_text(rollup(("ci", "COMPLETED", "SUCCESS")))

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


def calls(gh, prefix=""):
    log = Path(gh["log"])
    lines = log.read_text().splitlines() if log.exists() else []
    return [c for c in lines if c.startswith(prefix)]


def test_merge_lands_the_pr_and_then_fast_forwards_the_main_checkout(run, gh):
    write_receipt(run, gh["head"])
    before = git("rev-parse", "HEAD", cwd=run["main"])

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr

    assert calls(gh, "pr merge"), "the PR was never merged"
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
    assert calls(gh, "pr merge") == []
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
    assert calls(gh, "pr merge") == []


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
    assert calls(gh, "pr merge") == []


def test_merge_reports_not_merged_when_the_check_is_still_running(run, gh):
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks.json").write_text(rollup(("ci", "IN_PROGRESS", None)))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 3, r.stdout + r.stderr
    assert "NOT MERGED" in r.stderr
    assert "still running" in r.stderr
    assert calls(gh, "pr merge") == []
    assert git("rev-parse", "HEAD", cwd=run["main"]) == git(
        "rev-parse", "master", cwd=run["main"])


def test_merge_refuses_when_the_required_check_is_red(run, gh):
    write_receipt(run, gh["head"])
    (gh["dir"] / "checks.json").write_text(rollup(("ci", "COMPLETED", "FAILURE")))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not green" in r.stderr.lower()
    assert calls(gh, "pr merge") == []


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
    assert calls(gh, "pr merge")


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
    (gh["dir"] / "pr.json").write_text(
        json.dumps({"number": 7, "state": "OPEN", "headRefOid": head}))
    write_receipt(run, head)
    (gh["dir"] / "checks.json").write_text(
        rollup(("ci", "COMPLETED", "SUCCESS"), ("gate-x", "COMPLETED", "FAILURE")))

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "gate-x" in r.stderr
    assert calls(gh, "pr merge") == []


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
    assert calls(gh, "pr merge")
    assert git("rev-parse", "HEAD", cwd=run["main"]) == diverged


def test_a_failing_refresh_does_not_unwind_the_merge(run, gh):
    """Best-effort means the merge stands. It has already happened."""
    write_config(run["plan_wt"], refresh_commands=["exit 3"])
    commit_path(run["plan_wt"], "pitcall.config.json",
                "this project regenerates something after a pull")
    head = git("rev-parse", "HEAD", cwd=run["plan_wt"])
    (gh["dir"] / "pr.json").write_text(
        json.dumps({"number": 7, "state": "OPEN", "headRefOid": head}))
    write_receipt(run, head)

    r = finish(run["plan_wt"], "merge", PLAN, env=gh["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "refresh FAILED" in r.stdout
    assert git("rev-parse", "HEAD", cwd=run["main"]) == git(
        "rev-parse", "origin/master", cwd=run["main"])
