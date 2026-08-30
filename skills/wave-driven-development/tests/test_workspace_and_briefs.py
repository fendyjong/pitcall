"""Workspace resolution, brief extraction, and review packaging."""

import subprocess
from pathlib import Path

from conftest import run_script

PLAN = """\
# A Plan

### Task 1: First

Body of task one.

### Task 2: Second

Body of task two.

```markdown
### Task 3: Decoy inside a fence
```
"""


def _plan(repo) -> Path:
    p = repo.path / "plan.md"
    p.write_text(PLAN)
    return p


# --- wdd-workspace ---------------------------------------------------------


def test_workspace_prints_only_the_directory(repo):
    plan = _plan(repo)
    r = run_script("wdd-workspace", str(plan), cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    out = r.stdout.strip()
    assert out.endswith("/.pitcall/wdd/plan")
    assert Path(out).is_dir()
    # Exactly one line: callers do `dir=$(wdd-workspace plan.md)`.
    assert len(r.stdout.strip().splitlines()) == 1


def test_workspace_is_self_ignoring(repo):
    plan = _plan(repo)
    run_script("wdd-workspace", str(plan), cwd=repo.path, env=repo.env)
    assert (repo.path / ".pitcall/wdd/.gitignore").read_text() == "*\n"
    status = repo.git("status", "--porcelain").stdout
    assert ".pitcall" not in status


def test_workspace_is_idempotent(repo):
    plan = _plan(repo)
    first = run_script("wdd-workspace", str(plan), cwd=repo.path, env=repo.env)
    second = run_script("wdd-workspace", str(plan), cwd=repo.path, env=repo.env)
    assert first.stdout == second.stdout
    assert second.returncode == 0


def test_workspace_separates_plans(repo):
    a = _plan(repo)
    b = repo.path / "other.md"
    b.write_text(PLAN)
    ra = run_script("wdd-workspace", str(a), cwd=repo.path, env=repo.env)
    rb = run_script("wdd-workspace", str(b), cwd=repo.path, env=repo.env)
    assert ra.stdout != rb.stdout


def test_workspace_refuses_to_run_inside_a_task_worktree(repo):
    plan = _plan(repo)
    wt = repo.path / ".worktrees/plan-t2"
    repo.git("worktree", "add", "-q", "-b", "plan-t2", str(wt), "HEAD")
    (wt / "plan.md").write_text(PLAN)
    r = run_script("wdd-workspace", "plan.md", cwd=wt, env=repo.env)
    assert r.returncode == 2
    assert "task worktree" in r.stderr


def test_workspace_refuses_a_task_worktree_numbered_past_ninety_nine(repo):
    """Silently not guarding is the failure this guard exists to close.

    A one-or-two-digit suffix pattern stops matching at t100 and lets an
    implementer create the orphan workspace the controller cannot read.
    """
    plan = _plan(repo)
    wt = repo.path / ".worktrees/plan-t100"
    repo.git("worktree", "add", "-q", "-b", "plan-t100", str(wt), "HEAD")
    (wt / "plan.md").write_text(PLAN)
    r = run_script("wdd-workspace", "plan.md", cwd=wt, env=repo.env)
    assert r.returncode == 2
    assert "task worktree" in r.stderr


def test_workspace_accepts_a_plain_checkout_named_like_a_task_worktree(repo, tmp_path):
    """The guard keys on this plan's slug, not on any -t<N> suffix.

    A repository that merely happens to be named `legit-repo-t1` is nobody's
    task worktree, and locking it out entirely is worse than the failure the
    guard prevents.
    """
    other = tmp_path / "legit-repo-t1"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other, env=repo.env, check=True)
    (other / "plan.md").write_text(PLAN)
    r = run_script("wdd-workspace", "plan.md", cwd=other, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("/.pitcall/wdd/plan")


def test_workspace_missing_plan_exits_2(repo):
    r = run_script("wdd-workspace", "nope.md", cwd=repo.path, env=repo.env)
    assert r.returncode == 2


# --- task-brief ------------------------------------------------------------


def test_task_brief_extracts_only_the_named_task(repo):
    plan = _plan(repo)
    r = run_script("task-brief", str(plan), "1", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    body = (repo.path / ".pitcall/wdd/plan/task-1-brief.md").read_text()
    assert "Body of task one." in body
    assert "Body of task two." not in body


def test_task_brief_ignores_headings_inside_fences(repo):
    plan = _plan(repo)
    run_script("task-brief", str(plan), "2", cwd=repo.path, env=repo.env)
    body = (repo.path / ".pitcall/wdd/plan/task-2-brief.md").read_text()
    assert "Decoy inside a fence" in body  # it belongs to Task 2's body
    r = run_script("task-brief", str(plan), "3", cwd=repo.path, env=repo.env)
    assert r.returncode == 3


def test_task_brief_survives_a_fence_inside_a_fence(repo):
    """A four-backtick example containing a three-backtick one must not truncate.

    The upstream awk toggles on every ```-prefixed line, so it treats the
    heading inside the example as a real task heading and cuts the brief there
    — handing the implementer half its requirements.

    The ghost heading must sit INSIDE the inner ```python fence. One level down
    is where the naive toggle has flipped itself back to "outside"; at the outer
    level both trackers agree the line is fenced and the test proves nothing.
    Do not "tidy" this ordering — it is the whole test.
    """
    plan = repo.path / "nested.md"
    plan.write_text(
        "### Task 1: Real\n\n"
        "Opening body.\n\n"
        "````markdown\n"
        "```python\n"
        "### Task 42: ghost heading inside a nested fence\n"
        "```\n"
        "````\n\n"
        "Closing body that must survive.\n\n"
        "### Task 2: Next\n\nOther task.\n"
    )
    r = run_script("task-brief", str(plan), "1", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    body = (repo.path / ".pitcall/wdd/nested/task-1-brief.md").read_text()
    assert "Opening body." in body
    assert "Closing body that must survive." in body
    assert "Other task." not in body


def test_task_brief_reports_the_path_and_line_count(repo):
    plan = _plan(repo)
    r = run_script("task-brief", str(plan), "1", cwd=repo.path, env=repo.env)
    assert "task-1-brief.md" in r.stdout
    assert "lines" in r.stdout


def test_task_brief_missing_task_exits_3(repo):
    plan = _plan(repo)
    r = run_script("task-brief", str(plan), "9", cwd=repo.path, env=repo.env)
    assert r.returncode == 3
    assert "task 9 not found" in r.stderr


# --- review-package --------------------------------------------------------


def test_review_package_captures_every_commit_in_the_range(repo):
    plan = _plan(repo)
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    for name in ("one", "two"):
        (repo.path / f"{name}.txt").write_text(f"{name}\n")
        repo.git("add", "-A")
        repo.git("commit", "-qm", f"feat: add {name}")
    head = repo.git("rev-parse", "HEAD").stdout.strip()

    r = run_script("review-package", str(plan), base, head, cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert "2 commit(s)" in r.stdout

    written = Path(r.stdout.split()[1].rstrip(":"))
    body = written.read_text()
    assert "## Commits" in body
    assert "## Files changed" in body
    assert "## Diff" in body
    assert "feat: add one" in body
    assert "feat: add two" in body


def test_review_package_rejects_a_bad_base(repo):
    plan = _plan(repo)
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    r = run_script(
        "review-package", str(plan), "deadbeef", head, cwd=repo.path, env=repo.env
    )
    assert r.returncode == 2
    assert "bad BASE" in r.stderr
