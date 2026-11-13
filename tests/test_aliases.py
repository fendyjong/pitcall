"""deprecated(0.16.0): every 0.15.0 spelling still reaches its 0.16.0 command. Goes in 0.17.0.

One table drives it: each row is an old spelling, the new command it must
reach, and the two spellings the notice must name. An alias prints exactly one
line on stderr naming the new spelling and otherwise IS the new command: the
same stdout, the same exit status, and the new command's own stderr after the
notice. A call that spells two old names at once -- an old path and an old verb
-- still prints one notice, because the row's stderr must be exactly the notice
plus the new command's stderr.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD = "skills/wave-driven-development/scripts"
NEW = "skills/race/scripts"

PLAN = """\
# A Plan

## Waves

| Wave | Tasks | Rationale |
|---|---|---|
| 1 | 1 | the only task |

### Task 1: First

**Files:**
- Create: `app/one.py`

**Interfaces:**
- Depends: none
- Model: cheapest
- Produces: `one()`
"""

VERDICT = """\
Reviewed: b56f08775db75495b47fcebe192b46a1ddbf6416
Since: 2eb888e95f113117c63e0d526f68ad90267a45e6
Verdict: CLEAN
"""

#: (old command, new command, old spelling, new spelling, extra argument)
ROWS = [
    ((f"{OLD}/wdd", "check"), (f"{NEW}/race", "check"), "wdd check", "race check", None),
    ((f"{OLD}/wdd", "ship"), (f"{NEW}/race", "grid"), "wdd ship", "race grid", None),
    ((f"{OLD}/wdd", "merge"), (f"{NEW}/race", "finish"), "wdd merge", "race finish", None),
    ((f"{OLD}/wdd", "cleanup"), (f"{NEW}/race", "pack-down"), "wdd cleanup", "race pack-down", None),
    ((f"{OLD}/wave-worktree",), (f"{NEW}/lap-worktree",), "wave-worktree", "lap-worktree", None),
    ((f"{OLD}/wdd-ledger", "verdict"), (f"{NEW}/race-ledger", "verdict"),
     "wdd-ledger", "race-ledger", "verdict"),
    ((f"{OLD}/wdd-workspace",), (f"{NEW}/race-workspace",), "wdd-workspace", "race-workspace", None),
    ((f"{OLD}/plan-tasks", "--validate"), (f"{NEW}/qualifying",),
     "plan-tasks --validate", "qualifying", "plan"),
    ((f"{NEW}/plan-tasks", "--validate"), (f"{NEW}/qualifying",),
     "plan-tasks --validate", "qualifying", "plan"),
    ((f"{OLD}/plan-tasks",), (f"{NEW}/plan-tasks",), f"{OLD}/plan-tasks", f"{NEW}/plan-tasks", "plan"),
    ((f"{OLD}/project-config",), (f"{NEW}/project-config",),
     f"{OLD}/project-config", f"{NEW}/project-config", None),
    ((f"{OLD}/review-package",), (f"{NEW}/review-package",),
     f"{OLD}/review-package", f"{NEW}/review-package", None),
    ((f"{OLD}/task-brief",), (f"{NEW}/task-brief",), f"{OLD}/task-brief", f"{NEW}/task-brief", None),
    (("scripts/claim.py", "--help"), ("scripts/enter.py", "--help"), "claim.py", "enter.py", None),
    (("scripts/block.py", "--help"), ("scripts/red_flag.py", "--help"), "block.py", "red_flag.py", None),
    (("scripts/lane.py", "run", "--help"), ("scripts/pit.py", "lane", "--help"),
     "lane.py run", "pit.py lane", None),
    (("scripts/lane.py", "status", "--help"), ("scripts/pit.py", "status", "--help"),
     "lane.py status", "pit.py status", None),
    (("scripts/lane.py", "drop", "--help"), ("scripts/pit.py", "drop", "--help"),
     "lane.py drop", "pit.py drop", None),
    (("scripts/solution_ready.py", "--help"), ("scripts/green_flag.py", "--help"),
     "solution_ready.py", "green_flag.py", None),
]


def argv(command, extra):
    script, *args = command
    path = str(ROOT / script)
    head = [sys.executable, path] if script.endswith(".py") else [path]
    return [*head, *args, *extra]


def extra_args(kind, tmp_path):
    if kind is None:
        return []
    name, body = {"plan": ("plan.md", PLAN), "verdict": ("final-review-1.md", VERDICT)}[kind]
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return [str(path)]


@pytest.mark.parametrize("old, new, old_spelling, new_spelling, extra", ROWS,
                         ids=[row[2] for row in ROWS])
def test_an_old_spelling_is_the_new_command_plus_one_notice(
        tmp_path, old, new, old_spelling, new_spelling, extra):
    # Both files must exist, or two identical "no such file" failures would pass.
    assert (ROOT / old[0]).is_file(), old[0]
    assert (ROOT / new[0]).is_file(), new[0]
    args = extra_args(extra, tmp_path)
    was = subprocess.run(argv(old, args), cwd=tmp_path, capture_output=True, text=True)
    now = subprocess.run(argv(new, args), cwd=tmp_path, capture_output=True, text=True)
    notice = f"pitcall: '{old_spelling}' is deprecated and goes in 0.17.0; use '{new_spelling}'\n"
    assert (was.returncode, was.stdout) == (now.returncode, now.stdout)
    assert was.stderr == notice + now.stderr


#: The five old paths that were bash scripts in 0.15.0, called the way a bash script can be.
BASH_ROWS = [row for row in ROWS if row[0][0].rsplit("/", 1)[-1] in
             ("wdd", "wave-worktree", "wdd-workspace", "review-package", "task-brief")]


@pytest.mark.parametrize("old, new, old_spelling, new_spelling, extra", BASH_ROWS,
                         ids=[f"bash {row[2]}" for row in BASH_ROWS])
def test_an_old_bash_script_still_forwards_under_bash(
        tmp_path, old, new, old_spelling, new_spelling, extra):
    args = extra_args(extra, tmp_path)
    was = subprocess.run(["bash", str(ROOT / old[0]), *old[1:], *args],
                         cwd=tmp_path, capture_output=True, text=True)
    now = subprocess.run(argv(new, args), cwd=tmp_path, capture_output=True, text=True)
    notice = f"pitcall: '{old_spelling}' is deprecated and goes in 0.17.0; use '{new_spelling}'\n"
    assert (was.returncode, was.stdout) == (now.returncode, now.stdout)
    assert was.stderr == notice + now.stderr


def test_the_old_skill_shims_are_identical():
    shims = sorted(p for p in (ROOT / OLD).iterdir() if p.is_file())
    assert len(shims) == 8
    assert len({p.read_bytes() for p in shims}) == 1


def test_the_old_skill_sends_the_reader_to_race():
    text = (ROOT / "skills/wave-driven-development/SKILL.md").read_text(encoding="utf-8")
    assert "pitcall:race" in text


def test_the_old_command_runs_setup_with_the_same_tools():
    def allowed_tools(name):
        lines = (ROOT / "commands" / name).read_text(encoding="utf-8").splitlines()
        return next(line for line in lines if line.startswith("allowed-tools:"))

    assert allowed_tools("init.md") == allowed_tools("setup.md")
    assert "${CLAUDE_PLUGIN_ROOT}/commands/setup.md" in (ROOT / "commands/init.md").read_text(
        encoding="utf-8")
