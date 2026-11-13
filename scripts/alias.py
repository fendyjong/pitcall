"""deprecated(0.16.0): the 0.15.0 spellings of renamed commands. Goes in 0.17.0.

0.16.0 renamed the plugin's commands toward F1 names (issue #58). Every old
spelling keeps working for one release: it prints exactly one line on stderr
naming the new spelling, then becomes the new command (exec), so stdout and
the exit status are the new command's own. The notice goes to stderr only,
because callers read `project-config`, `race-workspace` and others by their
stdout.

Each old file is the same thin shim, calling `old_skill_script` or
`old_root_script` with its own file name; the two tables below are the whole
mapping.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REMOVED_IN = "0.17.0"
PLUGIN = Path(__file__).resolve().parents[1]
OLD_SKILL = "skills/wave-driven-development/scripts"
NEW_SKILL = "skills/race/scripts"

#: `skills/wave-driven-development/scripts/<old>` -> (new name, {old verb: new verb}).
SKILL_SCRIPTS = {
    "wdd": ("race", {"check": "check", "ship": "grid", "merge": "finish", "cleanup": "pack-down"}),
    "wave-worktree": ("lap-worktree", {}),
    "wdd-ledger": ("race-ledger", {}),
    "wdd-workspace": ("race-workspace", {}),
    "plan-tasks": ("plan-tasks", {}),
    "project-config": ("project-config", {}),
    "review-package": ("review-package", {}),
    "task-brief": ("task-brief", {}),
}

#: `scripts/<old>` -> (new name, {old subcommand: new subcommand}).
ROOT_SCRIPTS = {
    "claim.py": ("enter.py", {}),
    "block.py": ("red_flag.py", {}),
    "lane.py": ("pit.py", {"run": "lane", "status": "status", "drop": "drop"}),
    "solution_ready.py": ("green_flag.py", {}),
}


def notice(old: str, new: str) -> None:
    print(f"pitcall: '{old}' is deprecated and goes in {REMOVED_IN}; use '{new}'",
          file=sys.stderr, flush=True)


def forward(old: str, new: str, argv: list[str]) -> None:
    """Print the one notice for `old`, then replace this process with `argv`."""
    notice(old, new)
    os.execv(argv[0], argv)


def mapped(table: dict, name: str, args: list[str]) -> tuple[str, str, str, list[str]]:
    """`(old spelling, new spelling, new script, new args)` for one call of `name`.

    The spellings name the verb when the call used one, because that is what
    the caller has to change: `wdd merge` becomes `race finish`, not `race`.
    """
    new, verbs = table[name]
    if name == "plan-tasks" and args[:1] == ["--validate"]:
        return "plan-tasks --validate", "qualifying", "qualifying", args[1:]
    if args and args[0] in verbs:
        verb = verbs[args[0]]
        return f"{name} {args[0]}", f"{new} {verb}", new, [verb, *args[1:]]
    return name, new, new, args


def old_skill_script(name: str, args: list[str]) -> None:
    old, new, script, rest = mapped(SKILL_SCRIPTS, name, args)
    if old == new:
        # Moved, not renamed: the directory is what the caller must change.
        old, new = f"{OLD_SKILL}/{name}", f"{NEW_SKILL}/{name}"
    forward(old, new, [str(PLUGIN / NEW_SKILL / script), *rest])


def old_root_script(name: str, args: list[str]) -> None:
    old, new, script, rest = mapped(ROOT_SCRIPTS, name, args)
    forward(old, new, [sys.executable, str(PLUGIN / "scripts" / script), *rest])
