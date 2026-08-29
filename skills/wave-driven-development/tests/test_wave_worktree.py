"""Task worktree creation and teardown."""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import run_script

#: The four keys `load_config` requires of every project, plus the one
#: `wave-worktree` itself reads. `default_branch` matches the fixture repo's
#: branch, though this script never consults it.
CONFIG = {
    "bringup": None,
    "validate": "true",
    "default_branch": "main",
    "required_check": "ci",
    "worktree_dir": ".worktrees",
}


@pytest.fixture
def repo(repo):
    """Override the shared `repo` fixture: `wave-worktree` now refuses to run
    without a `pitcall.config.json` naming `worktree_dir` — a missing key is a
    loud failure, not a fallback to some default directory (see
    `scripts/project-config`). `worktree_dir` is set to `.worktrees` here
    purely to match the `.gitignore` the base fixture already writes and the
    literal paths these tests assert against; the script itself no longer
    hardcodes that value.
    """
    (repo.path / "pitcall.config.json").write_text(json.dumps(CONFIG))
    return repo


def _branches(repo) -> list[str]:
    out = repo.git("branch", "--format=%(refname:short)").stdout
    return [b.strip() for b in out.splitlines() if b.strip()]


def test_create_works_when_the_worktrees_dir_does_not_exist_yet(repo):
    """The first create in a fresh clone.

    The ignore guard must test a path INSIDE .worktrees. Testing the
    directory itself returns "not ignored" while it does not exist, because a
    trailing-slash gitignore pattern only matches a known directory — which
    would make the guard refuse every first run.
    """
    assert not (repo.path / ".worktrees").exists()
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    r = run_script("wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert (repo.path / ".worktrees/myplan-t1").is_dir()


def test_create_makes_worktree_and_branch_and_prints_path(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    r = run_script("wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr

    path = Path(r.stdout.strip())
    assert path == repo.path / ".worktrees/myplan-t1"
    assert (path / "seed.txt").exists()
    assert "myplan-t1" in _branches(repo)
    # stdout is the path and nothing else.
    assert len(r.stdout.strip().splitlines()) == 1


def test_create_branches_from_the_given_ref_not_from_head(repo):
    first = repo.git("rev-parse", "HEAD").stdout.strip()
    (repo.path / "later.txt").write_text("later\n")
    repo.git("add", "-A")
    repo.git("commit", "-qm", "later")

    r = run_script("wave-worktree", "create", "myplan", "1", first, cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert not (Path(r.stdout.strip()) / "later.txt").exists()


def test_create_refuses_when_the_branch_already_exists(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    run_script("wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env)
    run_script("wave-worktree", "remove", "myplan", "1", cwd=repo.path, env=repo.env)

    r = run_script("wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env)
    assert r.returncode == 3
    assert "already exists" in r.stderr
    assert "git branch -D myplan-t1" in r.stderr


def test_create_refuses_when_the_worktree_dir_is_not_ignored(repo):
    (repo.path / ".gitignore").write_text("# nothing ignored\n")
    repo.git("add", "-A")
    repo.git("commit", "-qm", "unignore worktrees")

    head = repo.git("rev-parse", "HEAD").stdout.strip()
    r = run_script("wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env)
    assert r.returncode == 4
    assert "not git-ignored" in r.stderr
    assert not (repo.path / ".worktrees/myplan-t1").exists()


def test_remove_deletes_the_worktree_but_keeps_the_branch(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    created = run_script(
        "wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env
    )
    path = Path(created.stdout.strip())

    r = run_script("wave-worktree", "remove", "myplan", "1", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert not path.exists()
    assert "myplan-t1" in _branches(repo)


def test_remove_refuses_to_discard_uncommitted_work(repo):
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    created = run_script(
        "wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env
    )
    path = Path(created.stdout.strip())
    (path / "unsaved.txt").write_text("an implementer's work\n")

    r = run_script("wave-worktree", "remove", "myplan", "1", cwd=repo.path, env=repo.env)
    assert r.returncode == 4
    assert "uncommitted changes" in r.stderr
    assert path.exists()
    assert (path / "unsaved.txt").exists()


def _add_submodule(repo, tmp_path: Path) -> None:
    """Give `repo` a real, committed submodule at `libs/sub`."""
    source = tmp_path / "subsource"
    source.mkdir()

    def git_in_source(*args: str) -> None:
        subprocess.run(["git", *args], cwd=source, env=repo.env, check=True)

    git_in_source("init", "-q", "-b", "main")
    (source / "s.txt").write_text("s\n")
    git_in_source("add", "-A")
    git_in_source("commit", "-qm", "s")

    # Local-path submodules are refused by default since CVE-2022-39253, and
    # `protocol.file.allow` is honoured only from system, global, command-line,
    # or env scope — never repo-local. `git config protocol.file.allow always`
    # here would be accepted and then silently ignored. The repo fixture
    # already points GIT_CONFIG_GLOBAL at a throwaway file, so writing it there
    # makes it global scope for every git call in this test, including the ones
    # wave-worktree itself makes inside the new worktree.
    Path(repo.env["GIT_CONFIG_GLOBAL"]).write_text(
        '[protocol "file"]\n\tallow = always\n'
    )
    repo.git("submodule", "add", "-q", str(source), "libs/sub")
    repo.git("commit", "-qm", "add submodule")


def test_create_and_remove_round_trip_with_a_submodule(repo, tmp_path):
    """The repo this script targets has submodules; git blocks removing them.

    `git worktree remove` fails on ANY worktree containing a submodule, clean or
    not, and `create` populates submodules by design — so a fixture without one
    cannot tell a working `remove` from a permanently broken one. That gap is
    how a green suite shipped a `remove` that could never succeed in practice.
    """
    _add_submodule(repo, tmp_path)

    head = repo.git("rev-parse", "HEAD").stdout.strip()
    created = run_script(
        "wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env
    )
    assert created.returncode == 0, created.stderr
    path = Path(created.stdout.strip())
    assert (path / "libs/sub/s.txt").exists(), "submodule was not initialised"

    removed = run_script("wave-worktree", "remove", "myplan", "1", cwd=repo.path, env=repo.env)
    assert removed.returncode == 0, removed.stderr
    assert not path.exists()
    assert "myplan-t1" in _branches(repo)


def test_remove_refuses_dirty_work_a_submodule_ignore_rule_would_hide(repo, tmp_path):
    """`submodule.<name>.ignore = all` blinds a plain `status --porcelain`.

    It lives in a committed `.gitmodules`, so any repo can introduce it, and a
    user's global `diff.ignoreSubmodules` does the same. Because the removal is
    forced, a blinded check would silently destroy an implementer's work — the
    one thing this script must never do.
    """
    _add_submodule(repo, tmp_path)
    repo.git("config", "-f", ".gitmodules", "submodule.libs/sub.ignore", "all")
    repo.git("add", ".gitmodules")
    repo.git("commit", "-qm", "ignore submodule changes")

    head = repo.git("rev-parse", "HEAD").stdout.strip()
    created = run_script(
        "wave-worktree", "create", "myplan", "1", head, cwd=repo.path, env=repo.env
    )
    assert created.returncode == 0, created.stderr
    path = Path(created.stdout.strip())

    work = path / "libs/sub/unsaved.txt"
    work.write_text("an implementer's work\n")

    removed = run_script("wave-worktree", "remove", "myplan", "1", cwd=repo.path, env=repo.env)
    assert removed.returncode == 4, removed.stderr
    assert "uncommitted changes" in removed.stderr
    assert work.exists(), "the forced removal discarded an implementer's work"


def test_remove_on_a_missing_worktree_exits_2(repo):
    r = run_script("wave-worktree", "remove", "myplan", "7", cwd=repo.path, env=repo.env)
    assert r.returncode == 2


def test_usage_error_exits_2(repo):
    r = run_script("wave-worktree", "create", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "usage:" in r.stderr
