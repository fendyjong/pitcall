"""Project config: where it is found, and what happens when it is incomplete.

This module ships in a plugin that is installed outside the project it
configures, so "where the config is" is now a question about the caller's
location rather than the module's. These tests build real repositories and ask
git the same question the module asks — a mocked answer would assert only that
the mock was written correctly.

The module is imported as a module, and its names are reached through it, so
that a symbol this task adds is missing at ASSERTION time rather than at import
time. A collection error fails the run too, but it fails every test in the file
at once and says nothing about which behaviour is absent.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lane_config  # noqa: E402

# Passed per-command so these repositories build on a machine with no global
# git config at all — a fresh CI runner has none, and `git commit` refuses
# without an author.
_GIT_ID = (
    "-c", "user.email=lane@example.invalid",
    "-c", "user.name=lane tests",
    "-c", "commit.gpgsign=false",
    "-c", "init.defaultBranch=main",
    # Adding a submodule from a local path is a file-transport clone, refused
    # by default since git 2.38.
    "-c", "protocol.file.allow=always",
)

# A complete config. Deliberately not this repository's own anything: the
# values are what a project would answer, and every test that cares about one
# key edits this copy rather than assuming a default.
_COMPLETE = {
    "bringup": "make up",
    "validate": "make test",
    "teardown": None,
    "default_branch": "main",
    "required_check": "build",
    "outbound_allowlist": ["15550001111", "15550002222"],
}


def _git(*args, cwd):
    out = subprocess.run(["git", *_GIT_ID, *args], cwd=str(cwd),
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _new_repo(path: Path) -> Path:
    """A real repository with one commit, resolved of any symlinks.

    The commit matters: a submodule cannot be added from a repository that has
    no HEAD.
    """
    path.mkdir(parents=True, exist_ok=True)
    path = path.resolve()
    _git("init", "-q", ".", cwd=path)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
    return path


def _write_config(root: Path, **overrides) -> Path:
    cfg = dict(_COMPLETE, **overrides)
    for key in [k for k, v in overrides.items() if v is _ABSENT]:
        del cfg[key]
    (root / lane_config.CONFIG_NAME).write_text(json.dumps(cfg, indent=2))
    return root


#: Sentinel for `_write_config`: this key is not merely empty, it is ABSENT.
#: The distinction is the whole point of the required-key check — a key present
#: and null is a project saying "none"; a key missing is a project that has not
#: been asked.
_ABSENT = object()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project the caller is standing in, with a complete config."""
    root = _write_config(_new_repo(tmp_path / "project"))
    monkeypatch.chdir(root)
    return root


# --- Finding the project -------------------------------------------------


def test_config_is_read_from_the_callers_project(project):
    assert lane_config.load_config()["bringup"] == "make up"


def test_the_config_is_the_one_committed_in_the_callers_own_checkout(tmp_path, monkeypatch):
    """A linked worktree is a branch, and the config is committed, so each branch
    carries its own copy.

    A branch that changes its validate command must be tested with that command,
    and a branch that widens the outbound allowlist must do so visibly in its own
    diff. Reading the main checkout's copy would test a branch against a config
    nobody on that branch wrote — and would look entirely normal, because in the
    main checkout the two paths are the same file.
    """
    main = _write_config(_new_repo(tmp_path / "project"), bringup="WRONG")
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=main)
    linked = linked.resolve()
    _write_config(linked, bringup="make up")
    assert linked != main, "the two checkouts must actually differ, or this proves nothing"

    monkeypatch.chdir(linked)
    assert lane_config.load_config()["bringup"] == "make up"


def test_a_submodules_own_config_never_shadows_the_superprojects(tmp_path, monkeypatch):
    """A session working inside a submodule is working on the project.

    The submodule here carries a config of its own — the shape a vendored
    dependency that is itself a project would have — and it must lose. Reading
    it instead would validate the dependency's commands while reporting on the
    project, with nothing erroring.
    """
    project = _write_config(_new_repo(tmp_path / "project"))
    dependency = _new_repo(tmp_path / "dependency")
    _git("submodule", "add", "-q", str(dependency), "vendor/dependency", cwd=project)
    inside = project / "vendor" / "dependency"
    # Written after the add, into the submodule's working tree: that is where a
    # session standing in the submodule would see it.
    _write_config(inside, bringup="WRONG")

    monkeypatch.chdir(inside)
    assert lane_config.load_config()["bringup"] == "make up"


def test_load_config_refuses_in_a_worktree_of_a_submodule_and_names_the_flag(
        tmp_path, monkeypatch):
    """The loud failure that a fix for a different defect quietly removed.

    This path used to raise only by accident — no config existed there, so
    reading one failed. Once the resolution learned to climb out of a
    submodule's git dir, the config came from the project's MAIN checkout
    instead: a different branch, plausibly stale, validated and reported green
    on code the session never wrote. The main checkout carries a distinguishable
    value here so a silent fallback cannot pass as success.

    The refusal names `--worktree`, so this also asserts that the fix the message
    advises actually works — an error telling a session to do something that
    does not help is worse than no message at all.
    """
    project = _write_config(_new_repo(tmp_path / "project"), bringup="MAIN-CHECKOUT")
    dependency = _new_repo(tmp_path / "dependency")
    _git("submodule", "add", "-q", str(dependency), "libs/a", cwd=project)
    sub_worktree = tmp_path / "submodule-worktree"
    _git("worktree", "add", "-q", str(sub_worktree), "-b", "side",
         cwd=project / "libs" / "a")
    sub_worktree = sub_worktree.resolve()
    _write_config(sub_worktree, bringup="SUBMODULE-WORKTREE")

    monkeypatch.chdir(sub_worktree)
    with pytest.raises(RuntimeError, match="--worktree"):
        lane_config.load_config()

    # An explicit checkout is taken at face value, not re-resolved — re-resolving
    # it would raise the very error the flag exists to settle.
    assert lane_config.load_config(sub_worktree)["bringup"] == "SUBMODULE-WORKTREE"


def test_load_config_outside_any_repository_raises_rather_than_searching_cwd(
        tmp_path, monkeypatch):
    """Same loud-failure stance the lane takes for an unresolvable lane.

    There is no directory to fall back to: a config picked up from wherever the
    caller happened to be standing would configure a bring-up against the wrong
    project, and look exactly like a correct one.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    _write_config(plain)          # present, and still must not be found
    monkeypatch.chdir(plain)
    probe = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                           cwd=str(plain), capture_output=True)
    assert probe.returncode != 0, "tmp_path is inside a repository — this test proves nothing"

    with pytest.raises(RuntimeError):
        lane_config.load_config()


def test_a_project_with_no_config_at_all_says_where_it_looked(tmp_path, monkeypatch):
    root = _new_repo(tmp_path / "project")
    monkeypatch.chdir(root)
    with pytest.raises(RuntimeError) as exc:
        lane_config.load_config()
    assert str(root) in str(exc.value), "the error must name the project it resolved"


def test_the_no_config_message_names_the_checkout_and_real_filenames(tmp_path, monkeypatch):
    """This string's correctness has depended on nobody reading it before.

    It used to report `path.parent` -- `.pitcall/` once neither location
    exists -- as though that directory were the checkout, and it named the
    LEGACY filename inside it: `.pitcall/pitcall.config.json`, a path neither
    the new nor the legacy location ever uses. Someone who created exactly
    that file would still have no working config.

    This pins the message to the checkout ROOT, and to the two filenames a
    project could actually write there.
    """
    root = _new_repo(tmp_path / "project")
    monkeypatch.chdir(root)
    with pytest.raises(RuntimeError) as exc:
        lane_config.load_config()
    message = str(exc.value)
    assert f"in {root} " in message, "must name the checkout root, not a directory inside it"
    assert f"{lane_config.CONFIG_DIR}/{lane_config.CONFIG_BASENAME}" in message
    assert lane_config.CONFIG_NAME in message
    assert f"{lane_config.CONFIG_DIR}/{lane_config.CONFIG_NAME}" not in message, (
        "must never name the legacy filename inside the new directory -- that "
        "path is not read by either location"
    )


# --- Resolving the config's location --------------------------------------


def test_new_location_is_preferred(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".pitcall").mkdir()
    (tmp_path / ".pitcall" / "config.json").write_text('{"default_branch": "main"}')
    assert lane_config.config_path(tmp_path) == tmp_path / ".pitcall" / "config.json"


def test_legacy_root_still_resolves_and_warns(tmp_path, capsys):
    (tmp_path / ".git").mkdir()
    (tmp_path / "pitcall.config.json").write_text('{"default_branch": "main"}')
    assert lane_config.config_path(tmp_path) == tmp_path / "pitcall.config.json"
    assert ".pitcall/config.json" in capsys.readouterr().err


def test_both_locations_present_is_an_error(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".pitcall").mkdir()
    (tmp_path / ".pitcall" / "config.json").write_text("{}")
    (tmp_path / "pitcall.config.json").write_text("{}")
    with pytest.raises(RuntimeError, match="two configs"):
        lane_config.config_path(tmp_path)


# --- A missing key is a loud failure, never a default --------------------


@pytest.mark.parametrize("key", ["bringup", "validate", "default_branch", "required_check"])
def test_a_missing_required_key_raises_and_names_itself(tmp_path, monkeypatch, key):
    """The plugin is generic: it cannot know that a project's default branch is
    `main`, or that its required check is called `build`. A default would cut a
    branch from the wrong base or gate a merge on a check that does not exist —
    both of which look like working behaviour right up until they do not."""
    root = _write_config(_new_repo(tmp_path / "project"), **{key: _ABSENT})
    monkeypatch.chdir(root)
    with pytest.raises(RuntimeError) as exc:
        lane_config.load_config()
    assert key in str(exc.value)


def test_every_missing_key_is_named_at_once(tmp_path, monkeypatch):
    """One key per run means one edit, one re-run, one more failure. Report the
    set, so a project is configured in a single pass."""
    root = _write_config(_new_repo(tmp_path / "project"),
                         default_branch=_ABSENT, required_check=_ABSENT)
    monkeypatch.chdir(root)
    with pytest.raises(RuntimeError) as exc:
        lane_config.load_config()
    assert "default_branch" in str(exc.value) and "required_check" in str(exc.value)


def test_a_required_key_may_be_explicitly_empty(tmp_path, monkeypatch):
    """Presence is what is checked, never truthiness.

    A project with nothing to run after bring-up writes `null` and says so; the
    lane already skips a step configured that way. Checking truthiness instead
    would make an explicit "none" indistinguishable from a key nobody wrote,
    which is the exact distinction this check exists to preserve.
    """
    root = _write_config(_new_repo(tmp_path / "project"), validate=None)
    monkeypatch.chdir(root)
    assert lane_config.load_config()["validate"] is None


# --- The outbound allowlist ----------------------------------------------


def test_allowlisted_accepts_the_designated_numbers(project):
    assert lane_config.allowlisted("15550001111")
    assert lane_config.allowlisted("15550002222")


def test_allowlisted_rejects_everything_else(project):
    # A real contact must never be reachable from an end-to-end run.
    assert not lane_config.allowlisted("15559998888")
    assert not lane_config.allowlisted("")


def test_allowlisted_does_not_normalise(project):
    # A '+' prefix or spacing is a DIFFERENT string. Normalising here would let a
    # near-miss through, and the whole guard is exact-match by design.
    assert not lane_config.allowlisted("+15550001111")
    assert not lane_config.allowlisted(" 15550001111")


def test_allowlisted_rejects_substring_and_superstring_variations(project):
    # Exact-match membership (set-based) protects against refactors that would
    # accept either superstrings or substrings of allowed numbers. This test
    # fails against both wrong directions:
    # - any(allowed in phone) accepts superstrings (allowed is a substring of input)
    # - any(phone in allowed) accepts substrings (input is a substring of allowed)
    # Either refactor silently breaks the guard and permits outbound to unvetted
    # numbers.
    assert not lane_config.allowlisted("155500011110"), "superstring must be rejected"
    assert not lane_config.allowlisted("1555000111100"), "superstring must be rejected"
    assert not lane_config.allowlisted("1555000111"), "substring must be rejected"
    assert not lane_config.allowlisted("155500011"), "substring must be rejected"
