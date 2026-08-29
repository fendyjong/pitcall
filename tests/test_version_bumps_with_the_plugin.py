"""The version cannot stand still while the plugin changes.

`.claude-plugin/plugin.json`'s `version` had been touched exactly twice, both at
creation, across 34 commits and four merged pull requests. That is not cosmetic:
the install cache is keyed on the version, so `/plugin update` reported "already
at the latest version" while the installed copy was 22 files behind — true about
the number, false about the contents.

The gate diffs against `origin/main`. When it cannot find that base it FAILS
rather than skipping, because a skip and a pass are the same line in CI output,
and the default `actions/checkout@v4` is shallow — so the skip would fire exactly
where the gate is meant to run.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ".claude-plugin/plugin.json"
BASE_REF = "origin/main"

# Only what an installed plugin executes. A README or a test changing does not
# change what a user runs, and a gate that fires on those becomes noise.
WATCHED = ("skills/", "commands/", "scripts/")


def _git(root, *args):
    return subprocess.run(
        ("git", "-C", str(root), *args), capture_output=True, text=True
    )


def base_sha(root):
    """The commit this branch diverged from, or a loud failure.

    Never returns None and never signals "unknown" — every caller would then have
    to decide what to do about it, and the tempting answer is to pass.
    """
    result = _git(root, "merge-base", BASE_REF, "HEAD")
    if result.returncode != 0:
        raise AssertionError(
            f"cannot resolve a base to diff against ({BASE_REF}): "
            f"{result.stderr.strip() or 'no such ref'}. This is a failure, not a "
            f"reason to skip: a shallow clone produces exactly this state, and "
            f"passing here would let an unbumped change through in the one "
            f"environment the gate exists for. CI must fetch full history."
        )
    return result.stdout.strip()


def touched_paths(root, base):
    out = _git(root, "diff", "--name-only", f"{base}..HEAD").stdout
    return [p for p in out.split("\n") if p.startswith(WATCHED)]


def version_at(root, ref):
    result = _git(root, "show", f"{ref}:{MANIFEST}")
    if result.returncode != 0:
        raise AssertionError(f"no {MANIFEST} at {ref}: {result.stderr.strip()}")
    return json.loads(result.stdout)["version"]


def version_now(root):
    return json.loads((Path(root) / MANIFEST).read_text())["version"]


def problems(root):
    """Empty list when the repository satisfies the gate."""
    base = base_sha(root)
    touched = touched_paths(root, base)
    if not touched:
        return []
    before, after = version_at(root, base), version_now(root)
    if before == after:
        return [
            f"{len(touched)} file(s) under {'/, '.join(WATCHED)} changed while "
            f"version stayed at {after}: {', '.join(sorted(touched)[:5])}"
        ]
    return []


def test_this_repository_satisfies_its_own_gate():
    assert problems(REPO_ROOT) == []


# --- fixture repositories, so the gate is exercised in both directions --------


def _repo(tmp_path, version="0.1.0"):
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / MANIFEST).write_text(json.dumps({"name": "p", "version": version}))
    _git(tmp_path, "init", "-q", str(root))
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", MANIFEST)
    _git(root, "commit", "-qm", "base")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    # A remote-tracking ref without a remote: enough for merge-base, and it keeps
    # the fixture offline.
    _git(root, "update-ref", f"refs/remotes/{BASE_REF}", head)
    return root


def _commit(root, rel, text, message):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(root, "add", rel)
    _git(root, "commit", "-qm", message)


def test_a_changed_skill_without_a_bump_fails(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "skills/x/SKILL.md", "changed\n", "touch a skill")
    found = problems(root)
    assert found, "a skill changed and the version did not — the gate must fail"
    assert "0.1.0" in found[0]


def test_the_same_change_with_a_bump_passes(tmp_path):
    root = _repo(tmp_path)
    (root / "skills" / "x").mkdir(parents=True)
    (root / "skills" / "x" / "SKILL.md").write_text("changed\n")
    (root / MANIFEST).write_text(json.dumps({"name": "p", "version": "0.2.0"}))
    _git(root, "add", "skills/x/SKILL.md", MANIFEST)
    _git(root, "commit", "-qm", "touch a skill and bump")
    assert problems(root) == []


def test_a_docs_only_change_needs_no_bump(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "README.md", "prose\n", "docs only")
    assert problems(root) == []


def test_it_fails_rather_than_skipping_without_a_base(tmp_path):
    root = _repo(tmp_path)
    _git(root, "update-ref", "-d", "refs/remotes/origin/main")
    _commit(root, "skills/x/SKILL.md", "changed\n", "touch a skill")
    with pytest.raises(AssertionError, match="cannot resolve a base"):
        problems(root)
