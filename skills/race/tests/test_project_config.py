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

import pytest

from conftest import run_script

REQUIRED = {
    "bringup": None,
    "validate": "true",
    "default_branch": "main",
    "required_check": "ci",
}


def _config(repo, **extra):
    """Write the config where `init` writes it: `.pitcall/config.json`."""
    _raw_config(repo, json.dumps({**REQUIRED, **extra}))
    return repo


def _raw_config(repo, text):
    d = repo.path / ".pitcall"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text(text)
    return d / "config.json"


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
    (other / ".pitcall").mkdir(parents=True)
    (other / ".pitcall" / "config.json").write_text(
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


# --- `--flag`: an optional boolean, answered three ways -----------------------
#
# `false` and `unset` are different facts — a project's decision, and no
# decision — and brainstorming's bounded path warns only on the second. So the
# cases that matter are the ones a two-valued reader would collapse.


def _flag(repo, key="bounded_via_wdd"):
    return run_script("project-config", "--flag", key, cwd=repo.path, env=repo.env)


def test_flag_prints_true(repo):
    _config(repo, bounded_via_wdd=True)
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "true\n"


def test_flag_prints_false(repo):
    _config(repo, bounded_via_wdd=False)
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "false\n"


def test_flag_reads_an_absent_key_as_unset(repo):
    _config(repo)
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "unset\n"


def test_flag_reads_null_as_unset(repo):
    """`null` means "no such setting" everywhere else in the config; here too."""
    _config(repo, bounded_via_wdd=None)
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "unset\n"


def test_flag_reads_a_checkout_with_no_config_as_unset(repo):
    """The one mode where no config is not an error.

    Brainstorming runs in projects that never ran `init`; a project with no
    config has made no decision, and failing would make every bounded task
    there stop to ask.
    """
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "unset\n"


@pytest.mark.parametrize("value", ["yes", "true", 1, ["true"]])
def test_flag_refuses_anything_but_a_json_boolean(repo, value):
    """A string "true" is not true: a flag that half-reads as true is worse than one that refuses."""
    _config(repo, bounded_via_wdd=value)
    r = _flag(repo)
    assert r.returncode == 2
    assert r.stdout == ""
    assert "bounded_via_wdd" in r.stderr


def test_flag_refuses_a_config_that_will_not_parse(repo):
    """A config that exists but cannot be read is an error, not "no decision"."""
    _raw_config(repo, "{not json")
    r = _flag(repo)
    assert r.returncode == 2
    assert r.stdout == ""


def test_flag_refuses_a_config_that_exists_but_cannot_be_read(repo):
    """An OSError on read (here: a directory at the config path) is unreadable, not "no decision"."""
    (repo.path / ".pitcall" / "config.json").mkdir(parents=True)
    r = _flag(repo)
    assert r.returncode == 2
    assert r.stdout == ""


#: Each passes the loader's required-key check by accident unless refused first:
#: `k in` a list or a string of the key names is true for every required key.
NOT_OBJECTS = [
    "null",
    "5",
    json.dumps(sorted(REQUIRED)),
    json.dumps(" ".join(sorted(REQUIRED))),
]


@pytest.mark.parametrize("raw", NOT_OBJECTS)
def test_flag_refuses_a_config_that_is_not_a_json_object(repo, raw):
    """A top level that is not an object is unreadable: exit 2, not a traceback."""
    _raw_config(repo, raw)
    r = _flag(repo)
    assert r.returncode == 2, r.stderr
    assert r.stdout == ""
    assert "Traceback" not in r.stderr


def test_scalar_refuses_a_config_that_will_not_parse_with_exit_2(repo):
    """The docstring's exit 2 for an unreadable config, held in every mode."""
    _raw_config(repo, "{not json")
    r = run_script("project-config", "--scalar", "default_branch",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 2, r.stderr
    assert r.stdout == ""
    assert "Traceback" not in r.stderr


def test_flag_prints_the_deprecation_notice_once(repo):
    """One resolution of the config, so one notice for a legacy location."""
    (repo.path / "pitcall.config.json").write_text(
        json.dumps({**REQUIRED, "bounded_via_wdd": True}))
    r = _flag(repo)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "true\n"
    assert r.stderr.count("deprecated") == 1


def test_a_refused_flag_prints_the_deprecation_notice_once(repo):
    (repo.path / "pitcall.config.json").write_text(
        json.dumps({**REQUIRED, "bounded_via_wdd": "yes"}))
    r = _flag(repo)
    assert r.returncode == 2
    assert r.stderr.count("deprecated") == 1
