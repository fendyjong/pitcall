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

A file whose name ends `.py` is skipped deliberately -- this keys on the
extension, not on being Python: `scripts/plan-tasks` and
`scripts/project-config` are `#!/usr/bin/env python3` with no `.py` suffix,
and ARE scanned. A `.py` file naming a script either executes it -- and fails
loudly on its own -- or discusses it historically, the way
`skills/wave-driven-development/tests/test_wdd.py` asserts the old `wdd-finish`
path must not come back. Flagging that would fire on the most correct code in
the repository, which is how a check gets switched off rather than fixed. Prose
that instructs a human lives in Markdown and in config comments, which is where
both original failures were. This file's own name ends `.py`, so the same
carve-out excludes it from its own scan -- `test_the_scan_skips_this_file`
pins that rather than leaving it to be rediscovered.
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
#
# The closing fence must be at least as long as the opener: `\1` backreferences
# the captured backtick run, so a fence opened with four backticks does not
# close on the first inner three-backtick fence it contains. Without this,
# `skills/wave-driven-development/SKILL.md`'s own four-backtick block --
# which itself contains three inner ``` fences, to document fenced examples --
# mis-pairs into fragments that drop real content between them.
INLINE = re.compile(r"`([^`\n]+)`")
FENCED = re.compile(r"^[ \t]*(`{3,})[^\n]*\n(.*?)^[ \t]*\1`*[ \t]*$", re.M | re.S)

# A `${VAR}/` prefix, any variable name -- see `normalize()`.
VAR_PREFIX = re.compile(r"^\$\{[^}]*\}/")

# Punctuation a name picks up from the sentence around it. Stripped from
# both ends by `clean()`'s trailing half; the leading half deliberately
# excludes `.` -- see `clean()`.
TRIM = "()[]{}<>,.;:!?'\"*"

# Same as TRIM, minus `.`: a leading `.` surviving to `clean()` is a real
# path character (a dot-directory like `.claude/...`), never a stray, because
# `normalize()` has already consumed `./` and `../` as whole prefixes by the
# time `clean()` runs.
LEADING_TRIM = TRIM.replace(".", "")


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
    for _delimiter, block in FENCED.findall(text):
        found.extend(line for line in block.split("\n") if line.strip())
    return found


def tokens(span):
    """Words in a span, exactly as the author wrote them."""
    return [t for t in span.split() if t]


def clean(token):
    """Strip punctuation a name picks up from the prose around it.

    Must run AFTER `normalize()`, never before: normalize() needs an intact
    leading `.` to recognise `./` or `../` as a whole prefix, and stripping
    it blindly (as this function alone used to, via the full `TRIM` set on
    both ends) mangled `./scripts/lane.py` into `/scripts/lane.py` -- a path
    the author never wrote -- before normalize() ever got a look. Once
    normalize() has run, any leading `.` that remains is a real character,
    not a stray, so the leading strip uses `LEADING_TRIM` instead of `TRIM`.
    Trailing punctuation carries no such ambiguity.
    """
    return token.lstrip(LEADING_TRIM).rstrip(TRIM)


def normalize(token):
    """Strip a citation prefix that reaches a real file through a path no
    tracked path spells: a plugin-root variable, a home-directory shorthand,
    a relative-path dot, or a unified-diff marker.

    Deliberately NOT a tail-only compare: an actually-wrong intermediate path
    segment -- e.g. `skills/brainstorming/scripts/wdd`, which names a file
    that lives under a different skill entirely -- must keep failing to
    resolve. Only these specific, whole-prefix shapes are stripped, and only
    from the front; nothing here touches the rest of the token.

    `${CLAUDE_PLUGIN_ROOT}/` is EXACTLY the prefix
    `test_every_command_reference_to_a_shipped_doc_is_plugin_rooted`
    (`test_plugin_closure.py`) REQUIRES on a `commands/*.md` file naming one
    of this plugin's own `docs/` files. That check and this one hold
    opposite opinions about the same string on purpose: it forces the prefix
    on, because a command's cwd is the project being configured, not this
    checkout; this one strips it, because a script path is meant to resolve
    inside this checkout either way. Whoever next touches either check
    should see the other.

    `a/` and `b/` (unified-diff prefixes) are safe to strip here only because
    no tracked path in this repo begins with either -- verified with
    `git ls-files | /usr/bin/grep -E '^(a|b)/'`, which returned nothing. If
    that ever changes, drop this branch rather than risk a false negative.
    """
    token = VAR_PREFIX.sub("", token, count=1)
    if token.startswith("~/"):
        token = token[2:]
    while token.startswith("./") or token.startswith("../"):
        token = token[2:] if token.startswith("./") else token[3:]
    if token.startswith("a/") or token.startswith("b/"):
        token = token[2:]
    return token


def resolves(token, tracked_paths):
    """A cited path resolves if a tracked path is it, or ends with it.

    Documents cite `scripts/plan-tasks` relative to the skill that owns it,
    while git tracks `skills/wave-driven-development/scripts/plan-tasks`.
    Requiring an exact match would flag every correct citation in the repo.
    Callers pass a token already run through `normalize()`, which handles
    the mirror case -- a citation LONGER than the tracked path, e.g.
    `${CLAUDE_PLUGIN_ROOT}/skills/wave-driven-development/scripts/wdd` --
    that plain suffix matching alone cannot.
    """
    return any(p == token or p.endswith("/" + token) for p in tracked_paths)


def problems_in(text, path, dead, tracked_paths):
    """The gate's whole judgement, over one document's text."""
    found = []
    for span in spans(text):
        for raw in tokens(span):
            if "<" in raw or ">" in raw:
                # A token holding a placeholder like `<name>`, or wrapped
                # whole in angle brackets like `<scripts/name>` (markdown's
                # own autolink shape), is prose showing the SHAPE of a path,
                # not a claim that one exists. Checked on `raw`, before any
                # stripping: `TRIM` would eat a bracket sitting at the
                # token's own edge and leave this guard blind to
                # `<scripts/name>` specifically. This idiom is already live
                # and correct in this repo: `wdd/<plan-slug>/progress.md`
                # (SKILL.md:817), `.pitcall/receipts/<sha>.json`
                # (SKILL.md:1131), and `repos/<owner>`
                # (commands/spec-review.md:28). A false positive on correct
                # prose is how a check gets switched off rather than fixed,
                # which is the failure this gate exists to avoid.
                continue
            if "://" in raw:
                # A URL names a remote resource, not a path in this
                # checkout. Stripping the scheme and host would leave a tail
                # like `fendyjong/pitcall/blob/main/scripts/lane.py`, which
                # resolves against nothing anyway -- skip it outright rather
                # than flag a citation that was never a claim about this
                # checkout's own tree.
                continue
            token = clean(normalize(raw))
            if token in dead:
                found.append(
                    f"{path}: `{raw}` is named here but no longer exists in "
                    f"any scripts/ directory. If this mention is "
                    f"deliberately historical (a rename note, a changelog "
                    f"entry), write it unbackticked -- this check exists "
                    f"because a backticked name reads as a live instruction."
                )
            elif CITED_PATH.search(token) and not resolves(token, tracked_paths):
                found.append(
                    f"{path}: `{raw}` names a path under scripts/ that no "
                    f"tracked file matches, or is not yet tracked"
                )
    return found


def scanned_files(tracked_paths):
    return [p for p in tracked_paths if not p.endswith(".py")]


def problems(root):
    """Empty list when every script named in prose exists."""
    require_full_history(root)
    tracked_paths = tracked(root)
    scanned = scanned_files(tracked_paths)
    assert scanned, "no non-.py tracked files to scan - the enumeration did not run"
    dead = names_ever(root) - names_now(root)
    found = []
    for rel in scanned:
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
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
    _out(tmp_path, "init", "-q", str(root))
    _out(root, "config", "user.email", "t@example.com")
    _out(root, "config", "user.name", "t")
    _out(root, "add", "scripts/old-name")
    _out(root, "commit", "-qm", "add")
    _out(root, "mv", "scripts/old-name", "scripts/new-name")
    _out(root, "commit", "-qm", "rename")
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
    _out(tmp_path, "init", "-q", str(src))
    _out(src, "config", "user.email", "t@example.com")
    _out(src, "config", "user.name", "t")
    _out(src, "add", "scripts/a")
    _out(src, "commit", "-qm", "one")
    (src / "scripts" / "b").write_text("y\n")
    _out(src, "add", "scripts/b")
    _out(src, "commit", "-qm", "two")

    dst = tmp_path / "shallow"
    _out(tmp_path, "clone", "-q", "--depth", "1", f"file://{src}", str(dst))
    with pytest.raises(AssertionError, match="shallow clone"):
        problems(dst)


def test_a_fence_nested_inside_a_longer_delimiter_fence_is_still_scanned():
    """A four-backtick fence containing an inner three-backtick fence.

    The closing must match the OPENER's length, not just any ``` at column 0
    -- otherwise the inner fence's own close/open lines get treated as the
    outer fence's boundary, and content between them (here, the dead name)
    falls in the gap between two mis-paired matches and is never scanned.
    """
    text = (
        "````markdown\n"
        "pre text\n"
        "```bash\n"
        "wdd-finish check\n"
        "```\n"
        "tail text\n"
        "````\n"
    )
    found = problems_in(text, "SKILL.md", {"wdd-finish"}, [])
    assert found, "a dead name inside a nested fence must not be dropped"
    assert "wdd-finish" in found[0]


def test_a_scripts_placeholder_is_not_a_path_claim():
    """`scripts/<name>` shows the SHAPE of a path; it names no file.

    The same idiom is already live and correct elsewhere in this repo --
    `wdd/<plan-slug>/progress.md`, `.pitcall/receipts/<sha>.json`,
    `repos/<owner>` -- so a future `scripts/<name>` must not fail CI, even
    though an unrelated bad path with the same prefix still must.
    """
    found = problems_in(
        "Name it `scripts/<name>` so the loader finds it.", "docs/x.md", set(), []
    )
    assert found == [], "a placeholder is not a claim that a file exists"

    found = problems_in("see `scripts/typo-name`", "docs/x.md", set(), ["skills/w/scripts/real"])
    assert found and "typo-name" in found[0], "a real bad path must still be caught"


def test_a_whole_placeholder_wrapped_in_angle_brackets_is_not_a_path_claim():
    """`<scripts/name>` -- markdown's own autolink shape -- is the same
    placeholder idiom wrapped whole, not `scripts/<name>` with the brackets
    on the last segment. `TRIM` strips a bracket sitting at either edge of a
    token BEFORE the placeholder guard used to look, so this shape tokenized
    to a clean `scripts/name` and fired. The guard must check `raw`, not the
    trimmed form.
    """
    found = problems_in("see `<scripts/name>` in the loader", "docs/x.md", set(), [])
    assert found == [], "a whole-token placeholder is not a claim that a file exists"


# --- normalize(): a citation prefix longer than the tracked path -------------


def test_a_plugin_root_variable_prefix_resolves():
    """`${CLAUDE_PLUGIN_ROOT}/` is the one spelling
    `test_every_command_reference_to_a_shipped_doc_is_plugin_rooted`
    (`test_plugin_closure.py`) requires when a `commands/*.md` file names one
    of this plugin's own `docs/` files. No `commands/*.md` names a script
    today, but the day one does, this is the spelling the existing rule
    mandates, and it must resolve here rather than fail CI.
    """
    found = problems_in(
        "run `${CLAUDE_PLUGIN_ROOT}/skills/wave-driven-development/scripts/wdd`",
        "commands/x.md", set(),
        ["skills/wave-driven-development/scripts/wdd"],
    )
    assert found == []


def test_a_dot_slash_relative_citation_resolves():
    found = problems_in(
        "run `./scripts/lane.py` to check it", "docs/x.md", set(), ["scripts/lane.py"],
    )
    assert found == []


def test_a_double_dot_relative_citation_resolves():
    found = problems_in(
        "see `../scripts/lane.py`", "skills/w/SKILL.md", set(), ["scripts/lane.py"],
    )
    assert found == []


def test_a_home_directory_install_path_resolves():
    found = problems_in(
        "run `~/.claude/plugins/pitcall/scripts/wdd`", "docs/x.md", set(),
        [".claude/plugins/pitcall/scripts/wdd"],
    )
    assert found == []


def test_a_unified_diff_path_prefix_resolves():
    """No tracked path in this repo begins `a/` or `b/` -- verified with
    `git ls-files | /usr/bin/grep -E '^(a|b)/'`, which returned nothing --
    so stripping this pair of prefixes cannot hide a real wrong path.
    """
    text = "```diff\n--- a/scripts/lane.py\n+++ b/scripts/lane.py\n```\n"
    found = problems_in(text, "docs/x.md", set(), ["scripts/lane.py"])
    assert found == []


def test_a_url_citing_a_script_path_is_not_flagged():
    found = problems_in(
        "see `https://github.com/fendyjong/pitcall/blob/main/scripts/lane.py`",
        "docs/x.md", set(), [],
    )
    assert found == []


def test_a_wrong_intermediate_path_still_fails_to_resolve():
    """`wdd` really does live under `wave-driven-development`; this cites the
    wrong skill entirely. Normalization strips known-transparent prefixes,
    never intermediate segments -- a tail-only compare would let this
    resolve too, which is exactly what must not happen.
    """
    found = problems_in(
        "see `skills/brainstorming/scripts/wdd`", "docs/x.md", set(),
        ["skills/wave-driven-development/scripts/wdd"],
    )
    assert found and "wdd" in found[0]
