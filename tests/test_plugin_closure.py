"""Reference closure: the plugin must work with no other plugin installed.

That property is static, not runtime — it asks only whether anything names a
skill, or points at a path, that is not here. A runtime check ("disable the
upstream plugin and see") proves it on one machine on one day; this proves it
on every commit.

Reads the git BLOB for each tracked path (`git cat-file -p :<path>`), never
`path.read_text()` on the working tree — same reason as
`tests/test_no_private_content.py`: a tracked symlink's blob content IS the
literal target string. Reading via the filesystem would instead follow the
link (or, for a dangling one, raise `FileNotFoundError`) and an
`except: continue` around that would silently drop the file from every
assertion rather than check it. Content that fails to decode as UTF-8 FAILS
the gate outright — "I could not read this" must never collapse into "this
reference is fine" for a check whose whole job is catching exactly that kind
of miss.

Four references can dangle, and the first version of this file caught only
one of them:

- `pitcall:<name>` — a skill or command invoked by name.
- a `../`-relative path in a markdown file. This is NOT covered by the name
  check: a bare relative path contains no `pitcall:` prefix, so it was
  invisible to it. A vendored skill arrived pointing at
  `../<a-skill-that-was-not-vendored>/references/` and every gate here passed,
  while `ATTRIBUTION.md` claimed every cross-reference had been rewritten.
  `../` is scanned rather than all link syntax because `../` is unambiguously
  a path — prose never contains it by accident — whereas a bare `](foo.md)`
  is one of several link spellings and a backticked word is not a link at all.
  Markdown only: a `../` inside a script is the language's own resolution at
  runtime (`require('../../..')`), not a documentation cross-reference, and
  it resolves against the installed layout rather than this tree.
- a skill DIRECTORY with no `SKILL.md`. This one is derived from the tracked
  PATHS rather than from any file's content, and it is why this gate scans
  paths at all: a half-vendored skill — scripts and prompts present, the one
  file the loader actually reads missing — is named by nothing, so no
  content scan can see it. It fails at load time, on the user's machine.
  (Scanning each path for `pitcall:` or the upstream prefix, the way the two
  sibling gates scan paths, was considered and rejected: a colon is not
  usable in a filename on every platform this plugin installs to, so such a
  path cannot arrive here. The half-vendored directory is the failure that
  actually lives in the path space.)
- a **bare** (non-`../`) path in `commands/*.md` naming one of this plugin's
  own `docs/` files. This is NOT covered by either check above: it has no
  `pitcall:` prefix and no `../`. A command executes with cwd set to the
  project it configures, not this checkout, so a bare `docs/configuration.md`
  is a plausible path THERE too, and resolves against a same-named file in
  that project instead of this plugin's own doc — silently, and for `init`,
  with write permission. This shipped once: `commands/init.md` named
  `docs/configuration.md` seven times, unqualified, and every check above
  passed.

`test_enumeration_is_not_empty` guards the gate itself. Without it a suite
that checks zero files exits 0 and is indistinguishable from a suite that
checked everything and found nothing — demonstrated on a `git archive` copy
with no tracked files, where both sibling gates failed and this one reported
`2 passed`.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = "tests/test_plugin_closure.py"
# Includes `_`: a reference like `pitcall:brainstorming_old` must capture
# the whole trailing token, not truncate at the underscore and accidentally
# resolve against a real, but different, skill.
REF = re.compile(r"\bpitcall:([a-z0-9_-]+)")
#: A `../`-relative path, stopping at whatever delimits it in prose — a
#: closing paren, a backtick, a quote or whitespace. `#` is excluded so a
#: link to a heading anchor resolves against the file, not the anchor.
RELATIVE = re.compile(r"\.\./[^\s`'\"()\[\]<>#]*")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def _files():
    """Every tracked file except this one, which names the tokens it forbids."""
    return [f for f in tracked_files() if f != SELF]


def _blob_text(rel):
    """Read the git-tracked blob for `rel`, not the working-tree file.

    A tracked symlink's blob content is its target path string; reading it
    via `Path.read_text()` would instead follow the link on disk (raising
    FileNotFoundError for a dangling one), which is exactly the failure mode
    that let a previous version of this file skip such entries entirely.
    """
    result = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=ROOT,
                            capture_output=True, check=True)
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        pytest.fail(
            f"{rel}: content is not valid UTF-8 - cannot verify its "
            "references, so the gate fails closed instead of skipping it"
        )


def test_enumeration_is_not_empty():
    """A gate that checks zero files exits 0 and looks exactly like a pass."""
    files = tracked_files()
    assert len(files) >= 5, f"only {len(files)} tracked file(s) - the gate did not run"


def test_no_upstream_plugin_is_named():
    for rel in _files():
        text = _blob_text(rel)
        assert "superpowers:" not in text, f"{rel} still names the upstream plugin"


def test_every_referenced_skill_exists():
    missing = set()
    for rel in _files():
        text = _blob_text(rel)
        for name in REF.findall(text):
            # A directory alone is not a skill - a partially-vendored or
            # emptied one would still pass `is_dir()` and only fail at
            # actual runtime resolution, the exact moment this test exists
            # to prevent. SKILL.md is the file the loader actually reads.
            has_skill = (ROOT / "skills" / name / "SKILL.md").is_file()
            has_command = (ROOT / "commands" / f"{name}.md").is_file()
            if not has_skill and not has_command:
                missing.add((rel, name))
    assert not missing, f"referenced but absent: {sorted(missing)}"


def test_every_relative_reference_resolves():
    """A `../` path in a doc must point at something this plugin ships.

    Resolved against the TRACKED paths, not the working tree: an untracked
    file on the author's disk is not published, so resolving against it would
    green-light a reference every user finds broken.
    """
    tracked = set(tracked_files())
    # Ancestor directories are not tracked entries but are legitimate targets.
    published = {"."} | tracked
    for rel in tracked:
        parent = os.path.dirname(rel)
        while parent:
            published.add(parent)
            parent = os.path.dirname(parent)

    dangling = set()
    for rel in _files():
        if not rel.endswith(".md"):
            continue
        for match in RELATIVE.finditer(_blob_text(rel)):
            # A trailing `/` marks a directory and a trailing `.` is the
            # sentence's, not the path's; neither is part of the target.
            target = match.group(0).rstrip("./")
            resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
            if resolved.startswith("..") or resolved not in published:
                dangling.add((rel, match.group(0)))
    assert not dangling, (
        "relative reference(s) pointing outside what this plugin ships: "
        f"{sorted(dangling)}"
    )


#: This plugin's own installed directory, wherever it was installed — the one
#: spelling that resolves the same way regardless of cwd. A command's cwd is
#: the ADOPTING PROJECT it is configuring, never this checkout.
PLUGIN_ROOT_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"


def test_every_command_reference_to_a_shipped_doc_is_plugin_rooted():
    """A `commands/*.md` file naming one of this plugin's own `docs/` files bare
    is the fourth dangling reference, and neither sibling gate above sees it.

    A command executes with cwd set to the project it is configuring, not this
    checkout. `docs/configuration.md` is a plausible path in a REAL project too
    -- so a bare mention resolves against a same-named file there instead of
    this plugin's own reference doc, and `init` (which literally executes what
    it reads) then reads a foreign file with write permission. This is exactly
    what shipped: `test_every_referenced_skill_exists` only matches
    `pitcall:name`, and `test_every_relative_reference_resolves` only matches a
    `../`-prefixed path, so a bare, non-relative `docs/configuration.md` was
    invisible to both until a live run outside this checkout exposed it.

    Scoped to paths tracked under this plugin's own `docs/` -- not every
    tracked path -- because a command legitimately names paths that must
    resolve in the ADOPTING PROJECT instead (`.gitignore`, `.pitcall/config.json`,
    `pitcall.config.json`), and qualifying THOSE with `${CLAUDE_PLUGIN_ROOT}`
    would be wrong, not safer: it would point them at this plugin's own
    checkout instead of the project being configured.
    """
    tracked = set(tracked_files())
    doc_paths = sorted((p for p in tracked if p.startswith("docs/")), key=len, reverse=True)
    assert doc_paths, "no docs/ files are tracked - the enumeration did not run"

    unqualified = set()
    for rel in _files():
        if not (rel.startswith("commands/") and rel.endswith(".md")):
            continue
        text = _blob_text(rel)
        for doc in doc_paths:
            start = 0
            while True:
                pos = text.find(doc, start)
                if pos == -1:
                    break
                start = pos + 1
                if text[:pos].endswith(PLUGIN_ROOT_PREFIX):
                    continue
                unqualified.add((rel, doc))
    assert not unqualified, (
        "command names a plugin doc bare, not qualified by "
        f"{PLUGIN_ROOT_PREFIX!r}, so it resolves against the ADOPTING "
        f"PROJECT's cwd instead of this plugin's own doc: {sorted(unqualified)}"
    )


def test_every_skill_directory_has_the_file_the_loader_reads():
    """A half-vendored skill is named by nothing, so no content scan finds it."""
    tracked = set(tracked_files())
    skill_dirs = {
        p.split("/")[1] for p in tracked
        if p.startswith("skills/") and p.count("/") >= 2
    }
    assert skill_dirs, "no skills are vendored - the enumeration did not run"
    missing = sorted(d for d in skill_dirs if f"skills/{d}/SKILL.md" not in tracked)
    assert not missing, f"skill directory with no SKILL.md: {missing}"
