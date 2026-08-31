"""A `../`-relative path inside a skill SCRIPT must resolve to something that exists.

The three existing reference gates are all scoped to prose, and each says so:

- `tests/test_plugin_closure.py` scans `../` in MARKDOWN, because "`../` is
  unambiguously a path — prose never contains it by accident".
- `tests/test_skill_script_paths_resolve.py` covers a bare `scripts/…` citation in a
  skill DOCUMENT.
- `tests/test_scripts_named_in_prose_exist.py` asks whether a cited script exists
  anywhere, from prose.

None of them reads an executable. That gap had no occupant until `wdd` began naming
`$HERE/../../../scripts/tracker.py`: the branch->issue parse is the inverse of
`branch_name`, so it belongs beside it in the plugin's own `scripts/`, and reaching it
is the first time a skill script names a file outside its own skill. A `../` hop in a
shell script dangles exactly the way one in a markdown file does — `no such file or
directory`, at ship time, in front of an operator trying to release — and nothing
caught it.

RESOLVED FROM THE SCRIPT'S OWN DIRECTORY, which is what makes the check meaningful:
these scripts compute their location at runtime (`HERE="$(cd "$(dirname "$0")" && pwd)"`,
`__dirname`) precisely so their relative paths are anchored there rather than to a
caller's cwd. The test resolves the same way.

Only the `../`-rooted SUFFIX of a token is taken, so a shell or JS variable ahead of it
(`$HERE/`, `path.join(__dirname, …)`) is ignored rather than parsed — this gate asks
"does the hop land somewhere", not "is the expression well-formed", and a parser for
every host language is the thing it must not become.

Reads the git BLOB (`git cat-file -p :<path>`), never the working tree, and FAILS on
content that will not decode as UTF-8 — the same two properties, for the same reasons,
as `tests/test_worktrees_have_one_home.py`: a tracked symlink's blob is its target
string, and "I could not read this" must never collapse into "this file is clean".
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The `../`-rooted tail of a path token: from the first `../` up to whatever ends the
# token in any of these host languages -- quote, whitespace, or a closing paren.
RELATIVE = re.compile(r"(\.\./[^\s'\"`)]*)")


def _tracked_skill_scripts():
    out = subprocess.run(
        ["git", "ls-files", "skills/*/scripts/*"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [p for p in out if not p.endswith(".md")]


def _blob(path):
    raw = subprocess.run(
        ["git", "cat-file", "-p", f":{path}"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout
    return raw.decode("utf-8")


def test_every_relative_hop_in_a_skill_script_lands_on_something():
    scripts = _tracked_skill_scripts()
    assert scripts, "no skill scripts found — the glob is wrong, not the repository"

    dangling = []
    for rel in scripts:
        here = (ROOT / rel).parent
        for hop in RELATIVE.findall(_blob(rel)):
            target = (here / hop).resolve()
            if not target.exists():
                dangling.append(f"{rel}: '{hop}' -> {target}")

    assert not dangling, "relative paths in skill scripts that resolve to nothing:\n" + "\n".join(dangling)
