"""Scratch lives in exactly one place: `.pitcall/`, never the upstream plugin's name.

This plugin's skills are derived from an upstream project (see `ATTRIBUTION.md`),
and the derivation rewrite was supposed to cover paths as well as prose. It missed
the scratch directory, which stayed on the upstream name in four behavioral lines
and sixteen mentions of prose. Nothing caught it, because the closure gate next
door tests for skill references (the upstream plugin's name immediately followed
by a colon) and a bare path segment does not match that.

Two homes for scratch is not cosmetic. `wdd-workspace` computes one path, while
`SKILL.md`'s ledger scan globs another literally; when the two drift, a controller
resuming a run finds no ledger and reports "nothing to resume", which invites a
fresh start over a run that was in fact resumable.

The bare word is deliberately still legal: `ATTRIBUTION.md` names the upstream
project twice, and must go on doing so. Only the dot-prefixed path spelling is
rejected, which separates the two cleanly and needs no exemption.

Reads the git BLOB for each tracked path (`git cat-file -p :<path>`), never
`path.read_text()` on the working tree:

- A tracked symlink's blob content IS the literal target string, so a symlink
  pointing into the rejected home is caught by content rather than followed on
  disk to whatever happens to be there.
- Content that fails to decode as UTF-8 FAILS the check. "I could not read this"
  must never collapse into "this file is clean" for a gate whose whole job is
  noticing a string nobody meant to commit.

Both the content and the file's own path are checked: a tracked file *under* the
rejected home is a second home whether or not it says so inside.

This file names the rejected path only in pieces (see `WRONG_HOME`), so it can be
scanned like every other file instead of needing an exemption — an exemption being
a hole somebody eventually writes content into.
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Spelled in pieces so this file can state the rejected path without tripping
#: its own check.
WRONG_HOME = "." + "superpowers"
RIGHT_HOME = ".pitcall"


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
            f"{rel}: content is not valid UTF-8 — cannot verify which scratch "
            "home it names, so the gate fails closed instead of skipping it"
        )


def test_enumeration_is_not_empty():
    """A gate that checks zero files exits 0 and looks exactly like a pass."""
    files = tracked_files()
    assert len(files) >= 5, f"only {len(files)} tracked file(s) — the gate did not run"


@pytest.mark.parametrize("rel", tracked_files())
def test_file_names_only_the_one_scratch_home(rel):
    assert WRONG_HOME not in rel, (
        f"{rel}: tracked under {WRONG_HOME!r} — scratch lives in "
        f"{RIGHT_HOME!r} and is never tracked"
    )

    text = blob_text(rel)
    assert WRONG_HOME not in text, (
        f"{rel}: names {WRONG_HOME!r}. Scratch has one home, {RIGHT_HOME!r} — "
        "a second one silently splits the step that creates a workspace from "
        "the ledger scan that is supposed to find it again."
    )
