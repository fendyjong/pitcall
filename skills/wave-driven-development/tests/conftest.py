"""Shared fixtures for the wave-driven-development script tests.

The scripts operate on real git repositories and real plan files, so the tests
drive them as subprocesses against throwaway repos rather than importing them.
That is the only way to exercise a bash script honestly.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@dataclass
class Repo:
    """A throwaway git repository plus the environment needed to drive it."""

    path: Path
    env: dict

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )


def run_script(name: str, *args: str, cwd: Path, env: dict | None = None):
    """Run one of the skill's scripts and return the completed process."""
    return subprocess.run(
        [str(SCRIPTS / name), *args],
        cwd=cwd,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """A git repo with one commit, isolated from the developer's real git config.

    GIT_CONFIG_GLOBAL/SYSTEM are redirected so the developer's own hooks,
    templates, and default branch name cannot change what these tests observe.
    """
    path = tmp_path / "repo"
    path.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    r = Repo(path=path, env=env)
    r.git("init", "-q", "-b", "main")
    (path / ".gitignore").write_text(".worktrees/\n")
    (path / "seed.txt").write_text("seed\n")
    r.git("add", "-A")
    r.git("commit", "-qm", "seed")
    return r
