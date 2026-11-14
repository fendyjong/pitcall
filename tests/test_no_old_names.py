"""No old name from before the 0.16.0 rename survives in a tracked file.

0.16.0 renamed the plugin's vocabulary toward F1 terms (issue #58) and kept
every old spelling working for one release, each compatibility block carrying a
deprecation marker (`MARKER`). 0.17.0 (issue #59) removed them all. This test
keeps an old name from coming back: it fails on one anywhere in a tracked file,
so a straggler in a long skill document fails CI instead of shipping, and it
fails on any file that still carries the marker -- the release's done check.

What may still spell an old name:

- the README's "Upgrading to ..." sections: history, left as written.
- this file, which has to spell what it looks for. It spells the marker in
  pieces, so the grep for the marker does not land here.

Reads each tracked path's git BLOB and fails on content that is not UTF-8, like
the repository's other gates: "I could not read this" must never pass as "this
is clean".

`python3 tests/test_no_old_names.py FILE...` scans just those files as they are
on disk and prints each hit: the check a change runs on its own files before it
commits. It exits 0 when clean, 1 when it found an old name, and 2 when it cannot
scan a path it was given.
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
    r"wave-driven-development|wave-worktree|(?<![A-Za-z0-9])waves?(?![A-Za-z0-9])|\bwdd\b"
    r"|implementer|bringup|teardown|solution-ready|solution_ready|\blane\.py\b"
    r"|(?<![A-Za-z0-9])lane[_\s]+run(?![A-Za-z0-9])|\bclaim\.py\b|\bblock\.py\b"
    r"|\bpitcall:init\b|--validate|receipt",
    re.IGNORECASE,
)

#: Old command names that are ordinary words too, so `OLD_NAMES` cannot look for
#: them. Checked only in agent-facing documents, and only in backticks, where
#: they can only mean the renamed command.
OLD_COMMAND_WORDS = re.compile(r"`(claim|block|ship|merge|cleanup|init)`")

UPGRADING = re.compile(r"^(#{1,6}) Upgrading to ")
HEADING = re.compile(r"^(#{1,6}) ")


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
    """`<rel>:<line>: <name>` for every old name outside the README's history."""
    if rel == SELF:
        return []
    lines = text.split("\n")
    history = upgrading_lines(lines) if rel == "README.md" else set()
    found, block = [], []
    for i, line in enumerate([*lines, ""]):
        if line.strip():
            block.append(i)
            continue
        if block:
            found += block_hits(rel, lines, [j for j in block if j not in history])
        block = []
    return found


def marked(rel, text):
    """True when `text` carries the 0.16.0 marker -- anywhere but in this file."""
    return rel != SELF and MARKER in text


def block_hits(rel, lines, block):
    """Old names in one block, matched across its line breaks: a wrap must not hide one."""
    starts, joined = [], ""
    for j in block:
        starts.append((len(joined), j))
        joined += lines[j] + "\n"
    found = []
    for m in OLD_NAMES.finditer(joined):
        line = max(j for offset, j in starts if offset <= m.start())
        found.append(f"{rel}:{line + 1}: {' '.join(m.group(0).split())}")
    return found


def is_agent_doc(rel):
    return rel.endswith(".md") and rel.startswith(("skills/", "commands/"))


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


def test_a_marker_no_longer_exempts_its_block():
    text = f"# {MARKER}: read until 0.17.0\nOLD = 'bringup'\n"
    assert hits("scripts/x.py", text) == ["scripts/x.py:2: bringup"]


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


def test_the_config_is_scanned_like_any_file():
    assert hits(".pitcall/config.json", '{"bringup": null}\n') == [".pitcall/config.json:1: bringup"]


def test_the_scan_reads_the_repository():
    """A gate that reads no files passes exactly like a clean one."""
    assert len(tracked()) >= 50


def test_no_old_name_in_a_tracked_file():
    found = [hit for rel in tracked() for hit in hits(rel, blob(rel))]
    assert not found, "old names in tracked files:\n" + "\n".join(found)


def test_marked_finds_the_marker_everywhere_but_here():
    assert marked("scripts/x.py", f"x = 1  # {MARKER}\n")
    assert not marked("scripts/x.py", "x = 1\n")
    assert not marked(SELF, f"MARKER = {MARKER!r}\n")


def test_no_tracked_file_carries_the_marker():
    """The 0.17.0 done check: a grep for the marker prints nothing."""
    found = [rel for rel in tracked() if marked(rel, blob(rel))]
    assert not found, f"still carrying {MARKER}: {found}"


def test_an_old_name_inside_an_identifier_is_found():
    assert hits("x.py", "def test_lane_run_ok():\n") == ["x.py:1: lane_run"]
    assert hits("x.py", "waves_dispatched = 1\nLAP = WAVE_WORKTREE\n") == [
        "x.py:1: waves", "x.py:2: WAVE"]


def test_an_old_name_split_across_a_line_break_is_found():
    assert hits("docs/x.md", "A commit pushed after a lane\nrun does not inherit it.\n") == [
        "docs/x.md:1: lane run"]


def test_kept_names_are_still_not_hits():
    text = "`bounded_via_wdd`, `WDD_CHECK_TIMEOUT` and `WDD_CHECK_POLL` keep their names.\n"
    assert hits("docs/x.md", text) == []


def test_old_command_words_are_caught_and_new_ones_are_not():
    assert OLD_COMMAND_WORDS.findall("`claim` cuts it; `ship` opens it; run `init`") == [
        "claim", "ship", "init"]
    assert OLD_COMMAND_WORDS.findall("`enter`, `grid`, `claim_expiry_hours`, `git merge`") == []


def test_the_cli_refuses_a_path_outside_the_repository(tmp_path, capsys):
    outside = tmp_path / "x.md"
    outside.write_text("wdd\n", encoding="utf-8")
    assert main([str(outside)]) == 2
    assert "cannot scan" in capsys.readouterr().err


def test_the_cli_refuses_a_missing_path(capsys):
    assert main([str(ROOT / "no-such-file")]) == 2
    assert "cannot scan" in capsys.readouterr().err


def test_agent_docs_name_the_new_commands():
    """Skill and command documents are instructions an agent follows, so an old
    command name there runs the alias, or a command that no longer exists. These
    words are ordinary English too, so the scanner above cannot look for them;
    in a backticked command position they can only mean the renamed command."""
    found = [f"{rel}:{n}: {m.group(0)}"
             for rel in tracked() if is_agent_doc(rel)
             for n, line in enumerate(blob(rel).split("\n"), 1)
             for m in OLD_COMMAND_WORDS.finditer(line)]
    assert not found, "old command names in agent-facing documents:\n" + "\n".join(found)


def main(paths):
    found = []
    for name in paths:
        path = Path(name).resolve()
        try:
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            # 2, not 1: exit 1 means "found an old name", and an unreadable path
            # must never read as a verdict either way.
            print(f"test_no_old_names: cannot scan {name}: {exc}", file=sys.stderr)
            return 2
        found += hits(rel, text)
    for hit in found:
        print(hit)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
