"""Reading the two project facts the workflow cannot guess.

`regenerated_paths` and `migration_homes` are optional, and the whole point of
their absence printing nothing is that the caller's

    project-config regenerated_paths | xargs -r git checkout --

skips the step by doing nothing. So the cases that matter here are the empty
answers: a key that is missing, and one written as `null`, must be
indistinguishable from an empty list at the caller. The error cases matter for
the opposite reason — a bare string would word-split into paths nobody wrote,
and this script's whole job is feeding paths to `git checkout`.
"""

import json

from conftest import run_script

REQUIRED = {
    "bringup": None,
    "validate": "true",
    "default_branch": "main",
    "required_check": "ci",
}


def _config(repo, **extra):
    (repo.path / "pitcall.config.json").write_text(json.dumps({**REQUIRED, **extra}))
    return repo


def test_prints_each_item_on_its_own_line(repo):
    _config(repo, regenerated_paths=["build/out/", "docs/generated/"])
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["build/out/", "docs/generated/"]


def test_a_missing_key_prints_nothing_and_succeeds(repo):
    """Absent means "this project has none", never "the config is broken"."""
    _config(repo)
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_an_explicit_null_reads_the_same_as_a_missing_key(repo):
    """`load_config` treats null as "there is no such step"; so must this.

    A project that writes the key out as null to document that it has none
    must not get a different answer from one that omitted it.
    """
    _config(repo, migration_homes=None)
    r = run_script("project-config", "migration_homes", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_an_empty_list_prints_nothing_and_succeeds(repo):
    _config(repo, migration_homes=[])
    r = run_script("project-config", "migration_homes", cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_a_bare_string_is_refused_rather_than_printed(repo):
    """`"a b"` printed as one line word-splits at the caller into two paths."""
    _config(repo, regenerated_paths="build/out/ docs/generated/")
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "must be a list" in r.stderr
    assert r.stdout == ""


def test_an_embedded_newline_is_refused_rather_than_split(repo):
    """One entry arriving at the caller as two is the same defect, one level in."""
    _config(repo, regenerated_paths=["build/out/\ndocs/generated/"])
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "newline" in r.stderr


def test_no_config_at_all_is_an_error_naming_the_checkout(repo):
    """Silence here would read as "this project declares none" — it is not.

    A project with no config has not answered the question; a project whose
    config omits the key has. Collapsing the two would restore the regenerated
    paths of a repository that has them into none, quietly.
    """
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "pitcall.config.json" in r.stderr
    assert r.stdout == ""


def test_an_explicit_checkout_is_read_instead_of_the_caller_s(repo, tmp_path):
    """The controller stands in the plan worktree and configures a task's.

    `worktree_root()` refuses to guess in some shapes, and the explicit
    argument is how a caller settles it — so it must be taken at face value.
    """
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "pitcall.config.json").write_text(
        json.dumps({**REQUIRED, "migration_homes": ["db/migrations"]})
    )
    _config(repo, migration_homes=["not/this/one"])

    r = run_script("project-config", "migration_homes", str(other),
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["db/migrations"]


def test_scalar_prints_a_single_string(repo):
    _config(repo, default_branch="trunk")
    r = run_script("project-config", "--scalar", "default_branch",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "trunk\n"


def test_scalar_refuses_a_missing_key_instead_of_printing_nothing(repo):
    """The asymmetry with list mode, and the reason for it.

    A caller asking "which branch?" cannot proceed without a name. Printing
    nothing would move the failure to the caller, where it arrives as an empty
    variable — and an empty base ref is how a branch gets checked against, or
    deleted on the strength of, the wrong thing.
    """
    _config(repo)
    r = run_script("project-config", "--scalar", "nothing_here",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "must be a non-empty string" in r.stderr
    assert r.stdout == ""


def test_scalar_refuses_an_empty_string(repo):
    """`BASE=""` reads as "no base" at every caller that does not check."""
    _config(repo, default_branch="")
    r = run_script("project-config", "--scalar", "default_branch",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert r.stdout == ""


def test_scalar_refuses_a_list(repo):
    """Two lines where the caller expected one is the list-mode defect inverted."""
    _config(repo, default_branch=["main", "master"])
    r = run_script("project-config", "--scalar", "default_branch",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert r.stdout == ""


def test_list_mode_still_refuses_a_string_without_the_flag(repo):
    """`--scalar` is opt-in: a list caller must never silently accept a string."""
    _config(repo, regenerated_paths="build/out/")
    r = run_script("project-config", "regenerated_paths", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "must be a list" in r.stderr


def test_usage_error_exits_2(repo):
    r = run_script("project-config", cwd=repo.path, env=repo.env)
    assert r.returncode == 2
    assert "usage:" in r.stderr
