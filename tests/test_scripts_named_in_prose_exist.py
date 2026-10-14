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

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional
from urllib.parse import unquote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_REL = "tests/test_scripts_named_in_prose_exist.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import lane_config  # noqa: E402

# A file directly inside any directory named `scripts`, at any depth. The repo
# has three such directories today and must not need editing when it has four.
SCRIPT_PATH = re.compile(r"(?:^|/)scripts/[^/]+$")

# The same shape as written in prose: a cited path whose last segment is a
# direct child of `scripts/`. Requiring that segment is what stops a bare
# `scripts/` -- which names a directory, not a file -- from being read as a
# claim about a file that must exist.
CITED_PATH = re.compile(r"(?:^|/)scripts/[^/\s]+$")

# Markdown inline code spans, and the body of fenced blocks. The leading
# `[ \t]*` is load-bearing: three of SKILL.md's fenced blocks are indented
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

# Where `repo_slug()` reads this repository's identity, and the one shape it
# accepts there. The name classes are GitHub's own, so a `#readme` or
# `?tab=` on the field cannot end up inside the slug -- a slug no link
# matches would make every URL read as foreign.
MANIFEST = ".claude-plugin/plugin.json"
GITHUB_REPO = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?")

# A git ref in a GitHub link, captured as one segment -- with the
# `refs/heads/` or `refs/tags/` qualifier GitHub's own Raw button writes,
# e.g. `raw/refs/heads/main/README.md`. A placeholder like `<sha>` is no ref.
REF = r"((?:refs/(?:heads|tags)/)?[^/<>\s]+)/"

# An abbreviated or full commit id: the one bare ref read as a commit
# rather than a name -- see `exists_at()`.
SHA = re.compile(r"[0-9a-f]{7,40}")

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


def repo_slug(root):
    """`owner/name` from the manifest's `repository`, or a loud failure.

    Read from a tracked file rather than `git remote get-url origin`, so the
    answer does not depend on how a clone's remote happens to be configured.
    Failing rather than returning None is the point: without a slug every URL
    reads as foreign, and the gate would pass while checking none of them --
    the same reasoning as `require_full_history`.
    """
    path = Path(root) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssertionError(f"cannot read {MANIFEST}: {exc}") from exc
    url = data.get("repository") if isinstance(data, dict) else None
    match = GITHUB_REPO.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        raise AssertionError(
            f"{MANIFEST} `repository` is {url!r}, not a "
            f"https://github.com/<owner>/<name> URL. A URL citation is checked "
            f"only when it points into this repository, so without that field "
            f"every one would pass unchecked."
        )
    return f"{match[1]}/{match[2]}"


class Repo(NamedTuple):
    """What a link into this repository is judged against.

    `slug` is `owner/name` (see `repo_slug()`) and `branch` the default
    branch. `exists_at(ref, path)` answers for a link pinned to any other
    ref -- see `link_resolves()`. Without it every pinned link is skipped.
    """

    slug: str
    branch: str
    exists_at: Optional[Callable[[str, str], Optional[bool]]] = None


def repo_link(token, slug):
    """`(ref, path)` for a link into `slug`, the path from the repository
    root, or None.

    A link into this repository is a claim this checkout can judge. Its known
    prefix -- `github.com/<owner>/<name>/(blob|tree|raw)/<ref>/` or
    `raw.githubusercontent.com/<owner>/<name>/<ref>/` -- is stripped whole,
    the way `normalize()` strips `${VAR}/` and `~/`, and no path segment past
    it is touched, so a wrong intermediate segment still fails to resolve.
    The link is searched for rather than anchored at the token's start, so
    `URL=https://...`, `--url=https://...` and `[text](https://...)` are
    still read; `clean()` then drops what the sentence left on its end.

    The path ends at a `#L10` line anchor or `?plain=1`, and at the quote or
    bracket that closes `<https://...>`, `[t](<https://...>)` or
    `href="https://..."`. It is percent-decoded, as GitHub decodes it --
    after the cut, so a `%3F` stays part of a name -- and a run of slashes
    reads as one.

    None means there is no claim to judge: the URL points anywhere else --
    another repository, another host, `http://host:port` -- whose tree this
    checkout cannot see; it is a link into this repository in a shape not
    listed above (a `blame/` page, say); its path holds a placeholder like
    `<name>`; or its path holds a `.` or `..` segment, which a browser
    resolves before GitHub ever sees it and neither judge here resolves.

    `<ref>` is read as one segment (see `REF`). A branch holding a slash
    (`feat/x`) is read as the ref `feat` with `x/` left on the path -- a
    branch name either way, so the link is skipped (see `exists_at()`).

    The match ignores case because GitHub resolves owner and repository
    names that way -- `FendyJong/Pitcall` is still this repository. The path
    after the prefix keeps its case; `link_resolves()` compares it exactly.
    """
    owner, name = (re.escape(part) for part in slug.split("/"))
    link = re.search(
        rf"https?://(?:(?:www\.)?github\.com/{owner}/{name}/(?:blob|tree|raw)/{REF}"
        rf"|raw\.githubusercontent\.com/{owner}/{name}/{REF})",
        token, re.IGNORECASE,
    )
    if link is None:
        return None
    ref = link[1] or link[2]
    path = unquote(re.split(r"[?#\"'>]", token[link.end():], maxsplit=1)[0])
    # GitHub serves `blob/main//scripts/x` as `blob/main/scripts/x`.
    path = re.sub(r"/{2,}", "/", path).lstrip("/")
    if "<" in path or any(part in (".", "..") for part in path.split("/")):
        return None
    return ref, clean(path)


def link_resolves(ref, path, tracked_paths, repo):
    """True or False for whether a link's `path` exists at its `ref`, or None
    when the gate cannot know and the link is skipped.

    Either way the path starts at the repository root and must name a file
    exactly: `blob/main/scripts/wdd` names a root-level `scripts/wdd`, and
    GitHub serves a 404 for it whatever lives under `skills/`.

    A link to the default branch or `HEAD` names the files this branch will
    merge, so it is judged against the tracked paths -- not git's copy of
    the default branch, which lacks a script this same branch adds and
    links. Any other ref names the files at that ref, which `exists_at`
    looks up: a permalink to a script renamed since stays quiet, and a wrong
    path in one still fails.
    """
    if ref in (repo.branch, "HEAD", f"refs/heads/{repo.branch}"):
        return path in tracked_paths
    if repo.exists_at is None:
        return None
    return repo.exists_at(ref, path)


def exists_at(root):
    """`Repo.exists_at` for a real checkout: git's answer at a commit or tag.

    None -- skip the link -- for a ref that is neither. A branch other than
    the default is skipped even when this clone has it: whether it does
    depends on what was fetched, so the same document could pass here and
    fail in CI. So is a sha git does not know: a typo, or a commit only an
    unmerged pull request holds, which GitHub still serves -- flagging it
    would fail CI on what may be a correct link. Two clones that both know a
    sha or tag answer alike; one that does not (`git clone --no-tags`, say)
    skips the link rather than contradicting the other.

    A tag link whose first path segment is missing at that tag is skipped
    too: with a tag `v2` and a branch `v2/hotfix`, `blob/v2/hotfix/...` is a
    link to the branch, which `REF` reads as the tag with `hotfix/` on the
    path. Only what `git ls-files` would list counts, as in the tracked-paths
    judge -- a file, or a submodule's gitlink: `cat-file -e` alone would also
    pass a directory.
    """
    def commit(ref):
        """`(sha, tagged)`, or None. A branch name is neither candidate."""
        name = ref.removeprefix("refs/tags/")
        candidates = [(f"refs/tags/{name}", True)]
        if name == ref and SHA.fullmatch(name):
            candidates.append((name, False))
        for candidate, tagged in candidates:
            result = _git(root, "rev-parse", "--verify", "--quiet",
                          f"{candidate}^{{commit}}")
            if result.returncode == 0:
                return result.stdout.strip(), tagged
        return None

    def kind(sha, path):
        if "\0" in path:
            return None  # a decoded `%00`: no object, and no argv either
        result = _git(root, "cat-file", "-t", f"{sha}:{path}")
        return result.stdout.strip() if result.returncode == 0 else None

    def answer(ref, path):
        found = commit(ref)
        if found is None:
            return None
        sha, tagged = found
        if tagged and kind(sha, path.split("/", 1)[0]) is None:
            return None
        return kind(sha, path) in ("blob", "commit")

    return answer


def this_repo(root):
    """The `Repo` a real checkout's links are judged against.

    `default_branch` is read through `lane_config`, the plugin's own config
    reader, rather than a second one here; it fails loudly for the same
    reason `repo_slug()` does.
    """
    try:
        branch = lane_config.load_config(root)["default_branch"]
    except RuntimeError as exc:
        raise AssertionError(f"cannot read default_branch: {exc}") from exc
    if not isinstance(branch, str) or not branch:
        raise AssertionError(
            f"default_branch is {branch!r}: a link to the default branch "
            f"cannot be told from a pinned one without it."
        )
    return Repo(repo_slug(root), branch, exists_at(root))


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

    `repo_link()` calls it without `normalize()`: the path it cuts out of
    a URL starts at the repository root, so there is no prefix to read.
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
    that plain suffix matching alone cannot. A path out of a URL is judged by
    `link_resolves()` instead: it starts at the repository root.
    """
    return any(p == token or p.endswith("/" + token) for p in tracked_paths)


def problems_in(text, path, dead, tracked_paths, repo=None):
    """The gate's whole judgement, over one document's text.

    `repo` is the `Repo` that decides which URLs are claims about this
    checkout. Without it every URL reads as foreign; `problems()` always
    passes one.
    """
    found = []
    for span in spans(text):
        for raw in tokens(span):
            if "://" in raw:
                # A URL into this repository is a claim about its tree and
                # is checked; any other URL is not, and is skipped. Skipping
                # every URL, as this once did, kept a correct link quiet but
                # swallowed a wrong one too (#17) -- see `repo_link()`. This
                # runs before the placeholder guard below, which would
                # swallow it again: the brackets and quotes around a link
                # (`<https://...>`, `href="..."`) are no placeholder, and
                # `repo_link()` finds one inside the link itself.
                link = repo_link(raw, repo.slug) if repo else None
                if link is None:
                    continue
                ref, target = link
                if (CITED_PATH.search(target)
                        and link_resolves(ref, target, tracked_paths, repo) is False):
                    found.append(
                        f"{path}: `{raw}` names a path under scripts/ that "
                        f"does not exist at {ref}"
                    )
                continue
            if "<" in raw or ">" in raw:
                # A token holding a placeholder like `<name>`, or wrapped
                # whole in angle brackets like `<scripts/name>`, is prose
                # showing the SHAPE of a path, not a claim that one exists.
                # Checked on `raw`, before any stripping: `TRIM` would eat a
                # bracket sitting at the token's own edge and leave this
                # guard blind to `<scripts/name>` specifically. This idiom
                # is already live and correct in this repo:
                # `wdd/<plan-slug>/progress.md` and
                # `.pitcall/receipts/<sha>.json` (both in WDD's SKILL.md),
                # and `repos/<owner>` (commands/spec-review.md). A false positive on correct
                # prose is how a check gets switched off rather than fixed,
                # which is the failure this gate exists to avoid.
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
    repo = this_repo(root)
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
        found.extend(problems_in(text, rel, dead, tracked_paths, repo=repo))
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
    """Three of SKILL.md's fences sit indented under numbered steps."""
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
    """`<scripts/name>` is the same placeholder idiom wrapped whole, not
    `scripts/<name>` with the brackets on the last segment. `TRIM` strips a
    bracket sitting at either edge of a token BEFORE the placeholder guard
    used to look, so this shape tokenized to a clean `scripts/name` and fired.
    The guard must check `raw`, not the trimmed form.
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


# --- URLs: a link into this repository names a path from its root ------------

REPO = Repo("fendyjong/pitcall", "main")
BLOB = f"https://github.com/{REPO.slug}/blob/main"


def test_a_correct_url_into_this_repo_is_not_flagged():
    found = problems_in(
        f"see `{BLOB}/scripts/lane.py`", "docs/x.md", set(), ["scripts/lane.py"],
        repo=REPO,
    )
    assert found == []


def test_a_url_into_this_repo_naming_a_missing_script_is_caught():
    """The negative control #17 found missing: every URL test asserted a
    correct citation stays quiet, and none asserted a wrong one still fails --
    which is how a skip that swallowed both survived its own round."""
    found = problems_in(
        f"see `{BLOB}/scripts/totally-made-up-name`", "docs/x.md", set(),
        ["skills/w/scripts/real"], repo=REPO,
    )
    assert found and "totally-made-up-name" in found[0]


def test_a_url_into_this_repo_with_a_wrong_intermediate_path_is_caught():
    """Resolving only the `scripts/<name>` tail would let this through:
    `wdd` is real, but under `wave-driven-development`, not `brainstorming`."""
    found = problems_in(
        f"see `{BLOB}/skills/brainstorming/scripts/wdd`", "docs/x.md", set(),
        ["skills/wave-driven-development/scripts/wdd"], repo=REPO,
    )
    assert found and "wdd" in found[0]


def test_a_url_is_anchored_at_the_repo_root():
    """A prose citation is relative to its own skill, so it may be a tracked
    path's tail. A URL is not: `blob/main/scripts/wdd` names a root-level
    `scripts/wdd`, which does not exist, and GitHub would serve a 404."""
    found = problems_in(
        f"see `{BLOB}/scripts/wdd`", "docs/x.md", set(),
        ["skills/wave-driven-development/scripts/wdd"], repo=REPO,
    )
    assert found and "wdd" in found[0]


def test_a_line_anchor_or_query_does_not_hide_the_path():
    tracked_paths = ["scripts/lane.py"]
    for suffix in ("#L10-L20", "?plain=1", "?plain=1#L3"):
        found = problems_in(
            f"see `{BLOB}/scripts/lane.py{suffix}`", "docs/x.md", set(),
            tracked_paths, repo=REPO,
        )
        assert found == [], f"a correct link with {suffix!r} must stay quiet"

    found = problems_in(
        f"see `{BLOB}/scripts/nope.py#L10`", "docs/x.md", set(),
        tracked_paths, repo=REPO,
    )
    assert found and "nope.py" in found[0], "a wrong link with an anchor must still fail"


def test_a_raw_content_url_is_checked_the_same_way():
    raw = f"https://raw.githubusercontent.com/{REPO.slug}/main"
    found = problems_in(
        f"fetch `{raw}/scripts/lane.py`", "docs/x.md", set(), ["scripts/lane.py"],
        repo=REPO,
    )
    assert found == []

    found = problems_in(
        f"fetch `{raw}/scripts/nope.py`", "docs/x.md", set(), ["scripts/lane.py"],
        repo=REPO,
    )
    assert found and "nope.py" in found[0]

    # Quoted, as a fenced `curl` line writes it: the quote must not hide the
    # scheme from the prefix match and turn a wrong link into a foreign one.
    found = problems_in(
        f'```bash\ncurl -fsSL "{raw}/scripts/nope.py"\n```\n', "docs/x.md", set(),
        ["scripts/lane.py"], repo=REPO,
    )
    assert found and "nope.py" in found[0]


# Every link shape the gate reads. Each is written once naming a tracked
# file and once naming a missing one, so a shape that silently stops being
# read fails the second half rather than passing both.
LINK_SHAPES = [
    "see `https://github.com/{repo}/blob/main/{path}`",
    "see `http://github.com/{repo}/blob/main/{path}`",
    "see `https://www.github.com/{repo}/blob/main/{path}`",
    "see `https://github.com/{repo}/tree/main/{path}`",
    "see `https://github.com/{repo}/raw/main/{path}`",
    # What GitHub's Raw button links to, and where it redirects.
    "see `https://github.com/{repo}/raw/refs/heads/main/{path}`",
    "see `https://raw.githubusercontent.com/{repo}/refs/heads/main/{path}`",
    "see `<https://github.com/{repo}/blob/main/{path}>`",
    "```markdown\nsee <https://github.com/{repo}/blob/main/{path}>.\n```\n",
    "see `(<https://github.com/{repo}/blob/main/{path}>)`",
    "see `[it](<https://github.com/{repo}/blob/main/{path}>)`",
    '```html\n<a href="https://github.com/{repo}/blob/main/{path}">it</a>\n```\n',
    '```html\n<img src="https://github.com/{repo}/raw/main/{path}"/>\n```\n',
    "```html\n<img src='https://github.com/{repo}/raw/main/{path}'/>\n```\n",
    "```html\n<td><https://github.com/{repo}/blob/main/{path}></td>\n```\n",
    "see `https://github.com/{repo}/blob/main//{path}`",
    "```markdown\nsee [it](https://github.com/{repo}/blob/main/{path})\n```\n",
    "```bash\nURL=https://raw.githubusercontent.com/{repo}/main/{path}\n```\n",
    "```bash\ntool --url=https://raw.githubusercontent.com/{repo}/main/{path}\n```\n",
]


@pytest.mark.parametrize("shape", LINK_SHAPES)
def test_every_link_shape_is_read(shape):
    tracked_paths = ["scripts/lane.py"]
    found = problems_in(
        shape.format(repo=REPO.slug, path="scripts/lane.py"), "docs/x.md", set(),
        tracked_paths, repo=REPO,
    )
    assert found == [], "a correct link must stay quiet"

    found = problems_in(
        shape.format(repo=REPO.slug, path="scripts/nope.py"), "docs/x.md", set(),
        tracked_paths, repo=REPO,
    )
    assert found and "nope.py" in found[0], "a wrong link must fail"


def test_a_placeholder_inside_a_link_is_not_a_claim():
    """The same idiom as `scripts/<name>` in a path, written inside a link.
    The lookup would call every one of these missing, so a placeholder that
    reached it would fail CI on correct prose."""
    repo = Repo(REPO.slug, "main", lambda ref, path: False)
    for link in (f"{BLOB}/scripts/<name>", f"{BLOB}/scripts/run-<name>",
                 f"https://github.com/{REPO.slug}/blob/<sha>/scripts/x"):
        assert problems_in(f"see `{link}`", "docs/x.md", set(), [], repo=repo) == [], link


def test_a_dot_segment_is_skipped_by_both_judges():
    """A browser resolves `./` before GitHub sees the path; neither judge
    here does, and they would disagree if either tried."""
    repo = Repo(REPO.slug, "main", lambda ref, path: False)
    for ref in ("main", "0123abcd"):
        for rest in ("./scripts/nope.py", "x/../scripts/nope.py"):
            link = f"https://github.com/{REPO.slug}/blob/{ref}/{rest}"
            assert problems_in(f"`{link}`", "d.md", set(), [], repo=repo) == [], link


def test_a_percent_encoded_path_is_decoded():
    tracked_paths = ["scripts/lane.py"]
    assert problems_in(f"`{BLOB}/scripts/lane%2Epy`", "d.md", set(),
                       tracked_paths, repo=REPO) == []
    found = problems_in(f"`{BLOB}/scripts/nope%2Epy`", "d.md", set(),
                        tracked_paths, repo=REPO)
    assert found and "nope%2Epy" in found[0]

    # Decoded after the cut: `%3F` is a `?` in the file name, not a query.
    found = problems_in(f"`{BLOB}/scripts/lane.py%3Fx`", "d.md", set(),
                        tracked_paths, repo=REPO)
    assert found and "lane.py%3Fx" in found[0]


def test_a_link_to_a_missing_file_outside_scripts_is_not_this_gates_claim():
    """The gate is about scripts; a missing doc is another check's business,
    and flagging it here would say `under scripts/` about a path that isn't."""
    assert problems_in(f"see `{BLOB}/docs/missing.md`", "d.md", set(), [],
                       repo=REPO) == []


def test_a_dot_in_the_repo_name_matches_only_a_dot():
    """Unescaped, `.` in `acme/w.x` would read `acme/wXx` -- another
    repository -- as this one, and judge its links against this tree."""
    repo = Repo("acme/w.x", "main")
    wrong = "see `https://github.com/{}/blob/main/scripts/nope`"
    assert problems_in(wrong.format("acme/wXx"), "docs/x.md", set(), [], repo=repo) == []
    found = problems_in(wrong.format("acme/w.x"), "docs/x.md", set(), [], repo=repo)
    assert found and "nope" in found[0]


def test_owner_case_does_not_turn_this_repo_into_another():
    """GitHub resolves owner and repo names case-insensitively, so this is
    still a link into this repository -- reading it as foreign would skip it."""
    found = problems_in(
        "see `https://github.com/FendyJong/Pitcall/blob/main/scripts/nope.py`",
        "docs/x.md", set(), ["scripts/lane.py"], repo=REPO,
    )
    assert found and "nope.py" in found[0]


def test_a_url_into_another_repository_is_not_checked():
    """This checkout cannot see another repository's tree, so a link into one
    is not a claim the gate can judge -- flagging it would fail CI on a correct
    citation, e.g. of the upstream project `ATTRIBUTION.md` credits."""
    found = problems_in(
        "see `https://github.com/obra/superpowers/blob/main/skills/x/scripts/y`",
        "docs/x.md", set(), ["scripts/lane.py"], repo=REPO,
    )
    assert found == []


# --- the ref: the default branch is this tree, a pinned ref is its own -------


def test_the_default_branch_is_the_tracked_tree():
    """`main` is no constant: the `Repo` says which branch is the default,
    and `HEAD` and `refs/heads/<default>` name the same tree."""
    repo = Repo("acme/widget", "trunk")
    for ref in ("trunk", "HEAD", "refs/heads/trunk"):
        url = f"https://github.com/acme/widget/blob/{ref}/scripts/{{}}"
        assert problems_in(f"`{url.format('real')}`", "d.md", set(),
                           ["scripts/real"], repo=repo) == [], ref
        found = problems_in(f"`{url.format('nope')}`", "d.md", set(),
                            ["scripts/real"], repo=repo)
        assert found and f"at {ref}" in found[0], ref


def test_a_pinned_link_is_judged_at_its_own_ref():
    """A sha or tag link names the files at that ref, not this tree's: a
    permalink to a script renamed since stays quiet, a wrong path in one
    still fails, and a ref the lookup cannot place is skipped."""
    asked = []

    def exists_at(ref, path):
        asked.append((ref, path))
        return {"scripts/old-name": True, "scripts/nope": False}.get(path)

    repo = Repo("acme/widget", "main", exists_at)
    url = "https://github.com/acme/widget/blob/{}/scripts/{}"
    tracked_paths = ["scripts/new-name"]

    def judge(ref, name):
        return problems_in(f"`{url.format(ref, name)}`", "d.md", set(),
                           tracked_paths, repo=repo)

    assert judge("0123abcd", "old-name") == [], "renamed since: still a correct permalink"
    found = judge("v1.0.0", "nope")
    assert found and "at v1.0.0" in found[0]
    assert judge("refs/tags/v1.0.0", "unknown") == [], "None skips the link"
    assert judge("feat/x", "old-name") == [], "a slashed branch is the ref `feat`"
    assert asked == [("0123abcd", "scripts/old-name"), ("v1.0.0", "scripts/nope"),
                     ("refs/tags/v1.0.0", "scripts/unknown"),
                     ("feat", "x/scripts/old-name")]


def test_without_a_lookup_every_pinned_link_is_skipped():
    """A pinned link is never judged against this tree: here the path is
    missing from it, and the link must still stay quiet."""
    found = problems_in(
        f"see `https://github.com/{REPO.slug}/blob/0123abcd/scripts/old-name`",
        "docs/x.md", set(), ["scripts/new-name"], repo=REPO,
    )
    assert found == []


def test_without_a_repo_every_url_is_foreign():
    """The rule tests above that predate URLs pass no repo; `problems()`
    always passes one, read from the manifest."""
    found = problems_in(
        f"see `{BLOB}/scripts/totally-made-up-name`", "docs/x.md", set(), [],
    )
    assert found == []


def _manifest(root, data):
    """`data` is written as JSON, or verbatim when it is already a string."""
    (root / ".claude-plugin").mkdir(parents=True)
    text = data if isinstance(data, str) else json.dumps(data)
    (root / ".claude-plugin" / "plugin.json").write_text(text)
    return root


@pytest.mark.parametrize("url", [
    "https://github.com/acme/widget",
    "https://github.com/acme/widget/",
    "https://github.com/acme/widget.git",
])
def test_repo_slug_reads_the_manifest(tmp_path, url):
    assert repo_slug(_manifest(tmp_path, {"repository": url})) == "acme/widget"


@pytest.mark.parametrize("data", [
    {},
    {"repository": "https://gitlab.com/acme/widget"},
    {"repository": {"type": "git", "url": "https://github.com/acme/widget"}},
    {"repository": "https://github.com/acme/widget#readme"},
    {"repository": "https://github.com/acme/widget?tab=readme"},
    "[1]",
    "{",
])
def test_an_unreadable_repository_fails_rather_than_skipping_urls(tmp_path, data):
    """Every URL would read as foreign, and the gate would pass while checking
    none of them -- the same shape `require_full_history` refuses. An
    AssertionError specifically, never a bare AttributeError or
    JSONDecodeError, so the failure names the manifest."""
    with pytest.raises(AssertionError, match="plugin.json"):
        repo_slug(_manifest(tmp_path, data))


def test_a_missing_manifest_fails_rather_than_skipping_urls(tmp_path):
    with pytest.raises(AssertionError, match="plugin.json"):
        repo_slug(tmp_path)


CONFIG = {"bringup": None, "validate": None, "default_branch": "trunk",
          "required_check": "ci"}


def _config(root, data=CONFIG):
    (root / ".pitcall").mkdir(parents=True)
    (root / ".pitcall" / "config.json").write_text(json.dumps(data))
    return root


def _repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    _out(tmp_path, "init", "-q", str(root))
    _out(root, "config", "user.email", "t@example.com")
    _out(root, "config", "user.name", "t")
    return root


def _commit(root, message):
    _out(root, "add", "-A")
    _out(root, "commit", "-qm", message)
    return _out(root, "rev-parse", "HEAD").strip()


def test_this_repo_reads_the_slug_and_the_default_branch(tmp_path):
    root = _config(_manifest(tmp_path, {"repository": "https://github.com/acme/widget"}))
    repo = this_repo(root)
    assert (repo.slug, repo.branch) == ("acme/widget", "trunk")


@pytest.mark.parametrize("data", [
    None,
    {k: v for k, v in CONFIG.items() if k != "default_branch"},
    {**CONFIG, "default_branch": ""},
    {**CONFIG, "default_branch": None},
    {**CONFIG, "default_branch": 5},
])
def test_an_unreadable_default_branch_fails_loudly(tmp_path, data):
    """Without it a link to the default branch cannot be told from a pinned
    one. An AssertionError, like every other refusal here, never a bare
    RuntimeError from the config reader."""
    _manifest(tmp_path, {"repository": "https://github.com/acme/widget"})
    if data is not None:
        _config(tmp_path, data)
    with pytest.raises(AssertionError, match="default_branch"):
        this_repo(tmp_path)


def test_exists_at_answers_for_a_sha_or_tag_and_skips_the_rest(tmp_path):
    root = _repo(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "old-name").write_text("x\n")
    first = _commit(root, "add")
    _out(root, "tag", "v1")
    _out(root, "mv", "scripts/old-name", "scripts/new-name")
    _commit(root, "rename")
    branch = _out(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    answer = exists_at(root)

    assert answer(first, "scripts/old-name") is True
    assert answer(first[:7], "scripts/old-name") is True, "an abbreviated sha"
    assert answer(first, "scripts/new-name") is False, "not there yet at that sha"
    assert answer("v1", "scripts/old-name") is True
    assert answer("refs/tags/v1", "scripts/new-name") is False
    assert answer(branch, "scripts/new-name") is None, "a branch is skipped"
    assert answer(f"refs/heads/{branch}", "scripts/new-name") is None
    assert answer("deadbeefdeadbeef", "scripts/old-name") is None, "an unknown sha"
    assert answer(first[:6], "scripts/old-name") is None, "too short to be read as a sha"
    assert answer(f"refs/tags/{first}", "scripts/old-name") is None, "no such tag"
    assert answer(first, "scripts") is False, "a directory is not a file"
    assert answer(first, "scripts/old-name\0") is False, "a decoded %00 is no crash"

    blob = _out(root, "rev-parse", "HEAD:scripts/new-name").strip()
    tree = _out(root, "rev-parse", "HEAD:scripts").strip()
    assert answer(blob, "scripts/new-name") is None, "a blob sha names no commit"
    assert answer(tree, "scripts/new-name") is None, "a tree sha names no commit"

    # A branch `v1/hotfix` beside the tag `v1`: `REF` reads the link as the
    # tag with `hotfix/` on the path, which cannot exist there.
    assert answer("v1", "hotfix/scripts/new-name") is None
    assert answer("v1", "scripts/nope") is False, "a wrong path at a tag still fails"
    assert answer(first, "nope/scripts/old-name") is False, "a sha holds no slash: judged"

    # A submodule's gitlink is listed by `git ls-files`, so the tracked-paths
    # judge resolves it; the pinned judge must agree.
    # Committed straight from the index: `_commit`'s `add -A` would stage the
    # gitlink's missing directory as a deletion.
    _out(root, "update-index", "--add", "--cacheinfo", f"160000,{first},scripts/sub")
    _out(root, "commit", "-qm", "gitlink")
    linked = _out(root, "rev-parse", "HEAD").strip()
    assert answer(linked, "scripts/sub") is True


def test_the_gate_itself_checks_urls(tmp_path):
    """`problems()` must build the `Repo` AND pass it on. The tests above
    drive `problems_in()` directly, and this repository cites no script by
    URL, so dropping any part of it would make URLs foreign again with CI
    green -- #17's false negative one level up. The default branch is not
    `main` here, so a hard-coded `main` fails this too."""
    root = _config(_manifest(_repo(tmp_path), {"repository": "https://github.com/acme/widget"}))
    (root / "scripts").mkdir()
    (root / "scripts" / "old-name").write_text("x\n")
    first = _commit(root, "add")
    _out(root, "mv", "scripts/old-name", "scripts/real")
    blob = "https://github.com/acme/widget/blob"
    (root / "README.md").write_text(
        f"see `{blob}/trunk/scripts/real`\n"
        f"and `{blob}/trunk/scripts/nope`\n"
        f"and `{blob}/{first}/scripts/old-name`\n"
        f"and `{blob}/{first}/scripts/nope`\n"
    )
    _commit(root, "rename")
    found = problems(root)
    assert len(found) == 2, found
    assert "trunk/scripts/nope" in found[0] and "at trunk" in found[0]
    assert f"{first}/scripts/nope" in found[1] and f"at {first}" in found[1]


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
