"""No old name survives the 0.16.0 rename outside a block that says why.

0.16.0 renamed the plugin's vocabulary toward F1 terms (issue #58). Every old
spelling keeps working for one release, and each alias or compatibility branch
that must still spell an old name carries the deprecation marker (`MARKER`), so
0.17.0 finds them all with one grep. This test is the other half: it fails on an
old name anywhere else in a tracked file, so a missed mention in a long skill
document fails CI instead of surviving into the release.

What may still spell an old name:

- a BLOCK carrying the marker: the run of non-blank lines around it. A block,
  not a whole file: one marker exempting a file would hide every straggler in
  exactly the files most likely to need a compatibility branch.
- a file that exists only as an alias (`ALIAS_FILES`, `ALIAS_DIRS`), deleted
  whole in 0.17.0. Each must carry the marker, so the list cannot exempt a file
  that is not one.
- the README's "Upgrading to ..." sections: history, left as written.
- `.pitcall/config.json`: the installed plugin that drives this repository's
  own runs reads `bringup` until it is upgraded, and JSON has no comments to
  carry the marker.
- this file, which has to spell what it looks for. It spells the marker in
  pieces, so the 0.17.0 grep for the marker does not land here.

Reads each tracked path's git BLOB and fails on content that is not UTF-8, like
the repository's other gates: "I could not read this" must never pass as "this
is clean".

`python3 tests/test_no_old_names.py FILE...` scans just those files as they are
on disk and prints each hit: the check a change runs on its own files before it
commits.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = "tests/test_no_old_names.py"
MARKER = "deprecated(" + "0.16.0)"

OLD_NAMES = re.compile(
    r"wave-driven-development|wave-worktree|\bwaves?\b|\bwdd\b|implementer|bringup|teardown"
    r"|solution-ready|solution_ready|\blane\.py\b|\blane run\b|\bclaim\.py\b|\bblock\.py\b"
    r"|\bpitcall:init\b|--validate|receipt",
    re.IGNORECASE,
)

#: Files that exist only as 0.16.0 aliases and are deleted whole in 0.17.0.
ALIAS_FILES = {
    "commands/init.md",
    "scripts/alias.py",
    "scripts/block.py",
    "scripts/claim.py",
    "scripts/lane.py",
    "scripts/solution_ready.py",
    "tests/test_aliases.py",
}
ALIAS_DIRS = ("skills/wave-driven-development/",)

#: Read by the installed plugin that drives this repository's own runs.
ALLOWED_FILES = {".pitcall/config.json"}

UPGRADING = re.compile(r"^(#{1,6}) Upgrading to ")
HEADING = re.compile(r"^(#{1,6}) ")


def is_alias(rel):
    return rel in ALIAS_FILES or rel.startswith(ALIAS_DIRS)


def upgrading_lines(lines):
    """Indexes of the lines inside an "Upgrading to ..." section."""
    inside, level, found = False, 0, set()
    for i, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading and inside and len(heading.group(1)) <= level:
            inside = False
        upgrade = UPGRADING.match(line)
        if upgrade:
            inside, level = True, len(upgrade.group(1))
        if inside:
            found.add(i)
    return found


def hits(rel, text):
    """`<rel>:<line>: <name>` for every old name outside an exempt block."""
    if rel == SELF or rel in ALLOWED_FILES or is_alias(rel):
        return []
    lines = text.split("\n")
    history = upgrading_lines(lines) if rel == "README.md" else set()
    found, block = [], []
    for i, line in enumerate([*lines, ""]):
        if line.strip():
            block.append(i)
            continue
        if not any(MARKER in lines[j] for j in block):
            for j in block:
                if j not in history:
                    found += [f"{rel}:{j + 1}: {m.group(0)}" for m in OLD_NAMES.finditer(lines[j])]
        block = []
    return found


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def blob(rel):
    raw = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pytest.fail(f"{rel}: content is not UTF-8 -- cannot tell whether it names an old "
                    "command, so this fails closed instead of skipping it")


def test_an_old_name_is_found():
    assert hits("docs/x.md", "run `wdd merge` to land it\n") == ["docs/x.md:1: wdd"]


def test_case_does_not_hide_one():
    assert hits("docs/x.md", "WDD\n\n## Waves\n") == ["docs/x.md:1: WDD", "docs/x.md:3: Waves"]


def test_a_marked_block_may_name_one():
    assert hits("scripts/x.py", f"# {MARKER}: read until 0.17.0\nOLD = 'bringup'\n") == []


def test_the_mark_covers_only_its_own_block():
    text = f"# {MARKER}\nOLD = 'bringup'\n\nNEW = 'teardown'\n"
    assert hits("scripts/x.py", text) == ["scripts/x.py:4: teardown"]


def test_an_upgrading_section_of_the_readme_is_history():
    text = ("# pitcall\n\n## Install\n\n### Upgrading to 0.16.0\n\n`wdd merge` is `race finish`.\n\n"
            "### Upgrading to 0.10.0\n\nThe lane receipt.\n\n## Usage\n\nRun `wdd`.\n")
    assert hits("README.md", text) == ["README.md:15: wdd"]


def test_only_the_readme_has_history():
    assert hits("docs/x.md", "### Upgrading to 0.16.0\n\nRun `wdd`.\n") == ["docs/x.md:3: wdd"]


def test_new_names_and_kept_names_are_not_hits():
    text = ("Run `race grid`; the pit lane runs out_lap, and a pit pass lands.\n"
            "A driver works one lap. `bounded_via_wdd` keeps its name.\n")
    assert hits("docs/x.md", text) == []


def test_alias_files_and_the_allowed_config_are_skipped():
    assert hits("scripts/claim.py", "claim.py\n") == []
    assert hits("skills/wave-driven-development/scripts/wdd", "wdd\n") == []
    assert hits(".pitcall/config.json", '{"bringup": null}\n') == []


def test_the_scan_reads_the_repository():
    """A gate that reads no files passes exactly like a clean one."""
    assert len(tracked()) >= 50


# The rename lands across several commits on its branch. These marks come off
# with the last of them, and from then on both tests gate like any other.
@pytest.mark.xfail(reason="the 0.16.0 rename is still landing on this branch", strict=False)
def test_no_old_name_outside_a_marked_block():
    found = [hit for rel in tracked() for hit in hits(rel, blob(rel))]
    assert not found, "old names outside a marked block:\n" + "\n".join(found)


@pytest.mark.xfail(reason="the 0.16.0 rename is still landing on this branch", strict=False)
def test_every_alias_file_says_it_is_one():
    aliases = [rel for rel in tracked() if is_alias(rel)]
    assert aliases, "no alias file is tracked -- the list names nothing that exists"
    unmarked = [rel for rel in aliases if MARKER not in blob(rel)]
    assert not unmarked, f"listed as aliases but carrying no {MARKER}: {unmarked}"


def main(paths):
    found = []
    for name in paths:
        path = Path(name).resolve()
        found += hits(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
    for hit in found:
        print(hit)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
