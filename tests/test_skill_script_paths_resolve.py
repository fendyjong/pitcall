"""A bare `scripts/…` path in a skill document must resolve from where it executes.

Skill documents are executable instructions, and they run with the cwd set to the
*adopting project* — not to this plugin. A bare `scripts/foo` in one therefore names
a directory belonging to somebody else's repository. It is correct only when the
document means its own skill's script, and even then nothing mechanical resolves
it — this repository's own prose does, at
`skills/wave-driven-development/SKILL.md:343-344`: "script paths here and in
Phase 2 are relative to this skill's own directory, wherever the plugin is
installed." A reader follows that sentence; there is no loader chdir that makes
a bare path land inside its own skill on its own. The mechanically-correct
spelling for an own-skill script is `${CLAUDE_SKILL_DIR}`, which Claude Code's
documentation resolves "regardless of the current working directory" — this
repository does not use it anywhere today, and this file does not require it,
but a future reader should know the escape hatch exists.

`skills/brainstorming/SKILL.md` shipped a bare `scripts/project-config`, which lives
in `wave-driven-development`. It fails loudly (`no such file or directory`) rather
than silently, which is why it was deferred rather than treated as critical — but it
is the same class as a command reading a foreign document.

Neither existing gate covers it, and the reasons are worth keeping:

- `tests/test_scripts_named_in_prose_exist.py` asks whether a cited script exists
  ANYWHERE — its `resolves()` accepts a path when any tracked path ends with it, so
  the offending citation matched WDD's copy and the gate stayed green.
- `tests/test_plugin_closure.py`'s plugin-rooted check is scoped to `commands/*.md`
  naming `docs/` paths, so skill documents sit outside it entirely.

Reads the git BLOB for each tracked path (`git cat-file -p :<path>`), never
`path.read_text()` on the working tree, and fails rather than skips on content that
is not valid UTF-8 — the same two properties, for the same reasons, as
`tests/test_worktrees_have_one_home.py`.

A Markdown file directly under `skills/` has no owning skill and therefore no
`scripts/` directory a bare path could resolve into, so any bare reference in one is
a violation rather than a skip. There is no such file today; the rule is stated so a
future one fails closed instead of silently passing.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: A `scripts/<name>` citation that is NOT already anchored. The lookbehind rejects a
#: match preceded by `/` (an absolute or plugin-rooted path), by `$` or `{` (a variable
#: expansion), or by a word character, `.` or `-` (a longer token that merely ends in
#: "scripts").
BARE = re.compile(r'(?<![/\w${.-])scripts/([A-Za-z0-9._-]+)')

SKILL_ROOT = "skills/"


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\n") if f]


def skill_docs():
    return [f for f in tracked_files()
            if f.startswith(SKILL_ROOT) and f.endswith(".md")]


def blob_text(rel):
    """The git-tracked blob for `rel`, decoded, or a failure — never a skip."""
    result = subprocess.run(["git", "cat-file", "-p", f":{rel}"], cwd=ROOT,
                            capture_output=True, check=True)
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        pytest.fail(
            f"{rel}: content is not valid UTF-8 — cannot verify which scripts it "
            "names, so the gate fails closed instead of skipping it"
        )


def test_enumeration_is_not_empty():
    """A gate that checks zero files exits 0 and looks exactly like a pass."""
    docs = skill_docs()
    assert len(docs) >= 5, f"only {len(docs)} skill document(s) — the gate did not run"


@pytest.mark.parametrize("rel", skill_docs())
def test_bare_script_paths_resolve_inside_their_own_skill(rel):
    parts = rel.split("/")
    owner = "/".join(parts[:2]) if len(parts) > 2 else None
    tracked = set(tracked_files())

    for match in BARE.finditer(blob_text(rel)):
        name = match.group(1)
        if owner is None:
            pytest.fail(
                f"{rel}: names `scripts/{name}` but sits directly under "
                f"{SKILL_ROOT!r}, so it belongs to no skill and has no "
                f"`scripts/` directory that path could resolve into. Spell it "
                f"`${{CLAUDE_PLUGIN_ROOT}}/…`."
            )
        own = f"{owner}/scripts/{name}"
        assert own in tracked, (
            f"{rel}: names `scripts/{name}`, which does not exist at {own!r}. "
            f"A skill document runs with the adopting project's cwd, so a bare "
            f"path resolves into THAT repository — this repository's own "
            f"convention (skills/wave-driven-development/SKILL.md:343-344) permits "
            f"a bare path only when it names its own skill's script. Spell it "
            f"`${{CLAUDE_PLUGIN_ROOT}}/…` instead."
        )
