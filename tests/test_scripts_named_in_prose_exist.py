"""A script named in prose must still exist.

A rename rots every sentence naming the old script, and those sentences are
instructions: a reader following one runs a command that does not exist and
concludes the plugin is broken rather than the sentence. Two references went
stale exactly that way -- `README.md` and `pytest.ini`, both naming
`wdd-finish` after it became `wdd` -- and both were found by hand.

Both sides are derived. Candidate names come from git history rather than a
list in this file, so a rename is tracked without editing anything here; the
files to scan come from `git ls-files`, so a new document is covered the day it
lands. An enumeration is what failed last time: the stale references sat in the
two files nobody thought to list.

`*.py` is skipped deliberately. A Python file naming a script either executes
it -- and fails loudly on its own -- or discusses it historically, the way
`skills/wave-driven-development/tests/test_wdd.py` asserts the old `wdd-finish`
path must not come back. Flagging that would fire on the most correct code in
the repository, which is how a check gets switched off rather than fixed. Prose
that instructs a human lives in Markdown and in config comments, which is where
both original failures were. This file is itself `.py`, so the same carve-out
excludes it from its own scan -- `test_the_scan_skips_this_file` pins that
rather than leaving it to be rediscovered.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_REL = "tests/test_scripts_named_in_prose_exist.py"

# A file directly inside any directory named `scripts`, at any depth. The repo
# has two such directories today and must not need editing when it has three.
SCRIPT_PATH = re.compile(r"(?:^|/)scripts/[^/]+$")

# The same shape as written in prose: a cited path whose last segment is a
# direct child of `scripts/`. Requiring that segment is what stops a bare
# `scripts/` -- which names a directory, not a file -- from being read as a
# claim about a file that must exist.
CITED_PATH = re.compile(r"(?:^|/)scripts/[^/\s]+$")

# Markdown inline code spans, and the body of fenced blocks. The leading
# `[ \t]*` is load-bearing: three of SKILL.md's ten fenced blocks are indented
# under numbered steps, and one of them cites `scripts/project-config` and
# nothing else. Anchoring the fence at column 0 makes those invisible -- the
# gate would skip exactly the citation shape it calls the strongest.
# A backticked token
# is a claim that the thing exists; the same letters in running prose are not,
# which is what keeps `WDD Phase 1` from tripping a gate that must still catch
# a backticked `wdd`.
INLINE = re.compile(r"`([^`\n]+)`")
FENCED = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)

# Punctuation a name picks up from the sentence around it.
TRIM = "()[]{}<>,.;:!?'\"*"


def _git(root, *args):
    return subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True
    )


def _out(root, *args):
    """stdout, or a loud failure naming the command.

    Every caller below would otherwise read an empty string as "nothing found",
    which is the shape that lets a broken git invocation pass for a clean scan.
    """
    result = _git(root, *args)
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def require_full_history(root):
    """Refuse to run against a shallow clone.

    `names_ever` is read out of git history, so a shallow clone yields a short
    one and renamed scripts silently stop being tracked -- the gate keeps
    passing while checking less. Same reasoning as `base_sha` in
    `test_version_bumps_with_the_plugin.py`: a check that cannot evaluate its
    condition must fail rather than return green.
    """
    if _out(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise AssertionError(
            "shallow clone: `names_ever` comes from git history, so a shallow "
            "clone shortens it and a renamed script stops being tracked. This "
            "is a failure, not a reason to skip -- CI must fetch full history."
        )


def tracked(root):
    return [p for p in _out(root, "ls-files").split("\n") if p]


def names_now(root):
    return {p.rsplit("/", 1)[-1] for p in tracked(root) if SCRIPT_PATH.search(p)}


def names_ever(root):
    """Every basename that has ever sat in a script directory.

    No `--diff-filter`: `AMD` omits renames (`R`), and a renamed script's new
    name would then be missing from this set entirely.
    """
    log = _out(root, "log", "--format=", "--name-only")
    return {p.rsplit("/", 1)[-1] for p in log.split("\n") if SCRIPT_PATH.search(p)}


def spans(text):
    """Inline code spans, plus each non-blank line of each fenced block."""
    found = INLINE.findall(text)
    for block in FENCED.findall(text):
        found.extend(line for line in block.split("\n") if line.strip())
    return found


def tokens(span):
    return [t for t in (raw.strip(TRIM) for raw in span.split()) if t]


def resolves(token, tracked_paths):
    """A cited path resolves if a tracked path is it, or ends with it.

    Documents cite `scripts/plan-tasks` relative to the skill that owns it,
    while git tracks `skills/wave-driven-development/scripts/plan-tasks`.
    Requiring an exact match would flag every correct citation in the repo.
    """
    return any(p == token or p.endswith("/" + token) for p in tracked_paths)


def problems_in(text, path, dead, tracked_paths):
    """The gate's whole judgement, over one document's text."""
    found = []
    for span in spans(text):
        for token in tokens(span):
            if token in dead:
                found.append(
                    f"{path}: `{token}` is named here but no longer exists in "
                    f"any scripts/ directory"
                )
            elif CITED_PATH.search(token) and not resolves(token, tracked_paths):
                found.append(
                    f"{path}: `{token}` names a path under scripts/ that no "
                    f"tracked file matches"
                )
    return found


def scanned_files(tracked_paths):
    return [p for p in tracked_paths if not p.endswith(".py")]


def problems(root):
    """Empty list when every script named in prose exists."""
    require_full_history(root)
    tracked_paths = tracked(root)
    dead = names_ever(root) - names_now(root)
    found = []
    for rel in scanned_files(tracked_paths):
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # a binary blob or a submodule gitlink names no scripts
        found.extend(problems_in(text, rel, dead, tracked_paths))
    return found


def test_this_repository_satisfies_its_own_gate():
    assert problems(REPO_ROOT) == []


def test_the_real_repository_has_dead_names_to_look_for():
    """Otherwise the gate above passes vacuously.

    `wdd-finish` (renamed to `wdd`) and `sdd-workspace` (removed) are both real.
    If this set ever empties, the test above stops proving anything, and this is
    the test that says so rather than letting a green run imply coverage.
    """
    dead = names_ever(REPO_ROOT) - names_now(REPO_ROOT)
    assert dead, "no script has ever been renamed or removed -- the gate is vacuous"


def test_the_scan_skips_this_file():
    """The `.py` carve-out also excludes this file, whose fixtures name dead scripts."""
    assert SELF_REL not in scanned_files(tracked(REPO_ROOT))


# --- the rules, driven directly, so each one is pinned on its own ------------


def test_a_renamed_script_named_in_markdown_is_caught():
    found = problems_in("run `wdd-finish merge` to land it", "README.md", {"wdd-finish"}, [])
    assert found, "a backticked dead script name must fail"
    assert "wdd-finish" in found[0]


def test_the_same_name_unbackticked_is_not_caught():
    found = problems_in("WDD Phase 1 mentions wdd-finish in prose", "README.md", {"wdd-finish"}, [])
    assert found == []


def test_a_fenced_block_is_scanned():
    found = problems_in("```bash\nwdd-finish check\n```\n", "SKILL.md", {"wdd-finish"}, [])
    assert found, "a fenced command line is the strongest form of 'run this'"


def test_an_indented_fenced_block_is_scanned():
    """Three of SKILL.md's ten fences sit indented under numbered steps."""
    found = problems_in("1. run it:\n\n   ```bash\n   wdd-finish check\n   ```\n",
                        "SKILL.md", {"wdd-finish"}, [])
    assert found, "a fence indented under a list item is still a fence"


def test_names_ever_sees_a_rename_target(tmp_path):
    """A renamed script's NEW name must be in `names_ever`.

    `--diff-filter=AMD` omits renames, so a script added, renamed, and never
    touched again vanishes from the set. In this repository `wdd` survives such
    a filter only because a later commit happened to modify it -- an accident of
    history, not a property to rely on -- so this is a purpose-built fixture
    rather than an assertion about the repo.
    """
    root = tmp_path / "r"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "old-name").write_text("x\n")
    _git(tmp_path, "init", "-q", str(root))
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "scripts/old-name")
    _git(root, "commit", "-qm", "add")
    _git(root, "mv", "scripts/old-name", "scripts/new-name")
    _git(root, "commit", "-qm", "rename")
    assert "new-name" in names_ever(root), "a reintroduced --diff-filter hides this"
    assert "old-name" in names_ever(root)


def test_a_path_under_scripts_that_does_not_resolve_is_caught():
    found = problems_in("see `scripts/typo-name`", "docs/x.md", set(), ["skills/w/scripts/real"])
    assert found and "typo-name" in found[0]


def test_a_relative_citation_resolves_against_a_nested_script_dir():
    found = problems_in(
        "run `scripts/plan-tasks`", "SKILL.md", set(),
        ["skills/wave-driven-development/scripts/plan-tasks"],
    )
    assert found == [], "documents cite script paths relative to their own skill"


def test_a_bare_scripts_directory_is_not_a_file_claim():
    found = problems_in("everything under `scripts/` ships", "docs/x.md", set(), [])
    assert found == []


def test_a_shallow_clone_fails_rather_than_passing_quietly(tmp_path):
    src = tmp_path / "src"
    (src / "scripts").mkdir(parents=True)
    (src / "scripts" / "a").write_text("x\n")
    _git(tmp_path, "init", "-q", str(src))
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    _git(src, "add", "scripts/a")
    _git(src, "commit", "-qm", "one")
    (src / "scripts" / "b").write_text("y\n")
    _git(src, "add", "scripts/b")
    _git(src, "commit", "-qm", "two")

    dst = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{src}", str(dst))
    with pytest.raises(AssertionError, match="shallow clone"):
        problems(dst)
