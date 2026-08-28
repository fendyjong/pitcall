"""Phase 3 — taking an integrated plan branch to a merged PR, then cleaning up.

The cases that matter are the ones where the ledger and git disagree. The
ledger is a controller's own account of what it did; git is what actually
happened. Every check here exists because trusting the first alone has a
failure mode nothing downstream would notice.
"""

import json
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


def finish(cwd, *args):
    """Run the real script from its real place in the plugin.

    Not a copy into the fixture repo: `wdd-finish` resolves its siblings
    through `$HERE`, and `project-config` resolves the config loader four
    levels up from itself. A copy flattens that layout, so the copy would pass
    while the shipped arrangement failed — the one difference a test of a
    script's own path resolution must not introduce.
    """
    return subprocess.run(
        ["bash", str(WDD_FINISH), *args],
        cwd=cwd, capture_output=True, text=True,
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
