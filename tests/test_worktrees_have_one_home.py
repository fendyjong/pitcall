"""Worktrees live in exactly one place: `.worktrees/` at the project root.

Two homes is not a cosmetic inconsistency. Every worktree-aware step in this
plugin is a pair of instructions that must agree on a path — a skill tells the
controller where to create a worktree, a script's ignore guard refuses unless
that directory is git-ignored, a cleanup step deletes what it finds under it.
When the two homes drift apart the failure is silent in the worst direction:
the guard checks one path, the creation uses the other, and worktrees get
created in a directory nothing ignores and nothing later cleans up. That is how
this repository inherited a skill whose own text pointed out that an upstream
cleanup step "only cleans worktrees under `.worktrees/` or `worktrees/`, which
never matches" the home it had chosen — a defect visible in prose for as long
as the prose survived, and enforced by nothing.

`.worktrees/` is the home. It is what `pitcall:brainstorming` defaults to, it is
what `.gitignore` ignores, and this test is what keeps the other spelling from
coming back in a paste.

Reads the git BLOB for each tracked path (`git cat-file -p :<path>`), never
`path.read_text()` on the working tree — the same two properties as
`tests/test_no_private_content.py`, and for the same demonstrated reasons:

- A tracked symlink's blob content IS the literal target string, so a symlink
  pointing into the rejected home is caught by content rather than followed on
  disk to whatever happens to be there.
- Content that fails to decode as UTF-8 FAILS the check. "I could not read
  this" must never collapse into "this file is clean" for a gate whose whole
  job is noticing a string nobody meant to commit.

Both the content and the file's own path are checked: a tracked file *under*
the rejected home is a second home whether or not it says so inside.

This file names the rejected path only in pieces (see `WRONG_HOME`), so it can
be scanned like every other file instead of needing an exemption — an
exemption being a hole somebody eventually writes content into.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Spelled in pieces so this file can state the rejected path without tripping
#: its own check.
WRONG_HOME = ".claude" + "/worktrees"
RIGHT_HOME = ".worktrees"


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def blob_text(rel):
    """The git-tracked blob for `rel`, decoded, or a failure — never a skip."""
    result = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=ROOT,
                            capture_output=True, check=True)
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        pytest.fail(
            f"{rel}: content is not valid UTF-8 — cannot verify which worktree "
            "home it names, so the gate fails closed instead of skipping it"
        )


def test_enumeration_is_not_empty():
    """A gate that checks zero files exits 0 and looks exactly like a pass."""
    files = tracked_files()
    assert len(files) >= 5, f"only {len(files)} tracked file(s) — the gate did not run"


@pytest.mark.parametrize("rel", tracked_files())
def test_file_names_only_the_one_worktree_home(rel):
    assert WRONG_HOME not in rel, (
        f"{rel}: tracked under {WRONG_HOME!r} — worktrees live in "
        f"{RIGHT_HOME!r} and are never tracked"
    )

    text = blob_text(rel)
    assert WRONG_HOME not in text, (
        f"{rel}: names {WRONG_HOME!r}. Worktrees have one home, {RIGHT_HOME!r} — "
        "a second one silently splits the create step from the ignore guard "
        "and the cleanup step that are supposed to agree with it."
    )
