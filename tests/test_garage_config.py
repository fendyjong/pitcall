"""The garage's paths, machine config and repo `runner` section (Task 2)."""

import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
import garage_config  # noqa: E402
import garage_paths  # noqa: E402
from garage_config import ConfigError, RunnerSection  # noqa: E402

HOME = {"HOME": "/home/op"}


def machine(**over):
    cfg = {"runners": 2, "token_file": "~/.config/pitcall/garage.token",
           "recipe": {name: f"/r/{name}.sh" for name in garage_config.RECIPE_COMMANDS}}
    cfg.update(over)
    return cfg


def write(tmp_path, cfg):
    path = tmp_path / "garage.json"
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    return path


# --- paths --------------------------------------------------------------------

def test_paths_default_under_home():
    assert garage_paths.machine_config_path(HOME) == Path("/home/op/.config/pitcall/garage.json")
    assert garage_paths.install_dir(HOME) == Path("/home/op/.local/share/pitcall/garage")
    assert garage_paths.state_file_path(HOME) == Path("/home/op/.local/state/pitcall/garage.json")


def test_paths_honour_xdg_variables():
    env = {**HOME, "XDG_CONFIG_HOME": "/x/c", "XDG_DATA_HOME": "/x/d", "XDG_STATE_HOME": "/x/s"}
    assert garage_paths.machine_config_path(env) == Path("/x/c/pitcall/garage.json")
    assert garage_paths.install_dir(env) == Path("/x/d/pitcall/garage")
    assert garage_paths.state_file_path(env) == Path("/x/s/pitcall/garage.json")


def test_an_empty_or_relative_xdg_variable_means_the_default():
    assert garage_paths.config_home({**HOME, "XDG_CONFIG_HOME": ""}) == Path("/home/op/.config")
    assert garage_paths.data_home({**HOME, "XDG_DATA_HOME": "rel"}) == Path("/home/op/.local/share")


def test_expand_replaces_only_a_leading_tilde():
    assert garage_paths.expand("~/.config/pitcall/garage.token", HOME) == Path(
        "/home/op/.config/pitcall/garage.token")
    assert garage_paths.expand("~", HOME) == Path("/home/op")
    assert garage_paths.expand("/abs/~/x", HOME) == Path("/abs/~/x")


# --- machine config -----------------------------------------------------------

def test_defaults_are_filled_and_written_keys_kept():
    cfg = garage_config.parse_machine_config(json.dumps(machine(poll_seconds=30)))
    assert cfg["idle_timeout_minutes"] == 10
    assert cfg["pickup_deadline_minutes"] == 5
    assert cfg["poll_seconds"] == 30
    assert cfg["repos"] == {}


@pytest.mark.parametrize("over, key", [
    ({"runners": 0}, "runners"),
    ({"runners": True}, "runners"),
    ({"runners": "3"}, "runners"),
    ({"recipe": {"start": "a", "stop": "b", "exec": "c"}}, "recipe.cleanup"),
    ({"recipe": {"start": "a", "stop": "b", "exec": " ", "cleanup": "d"}}, "recipe.exec"),
    ({"token_file": ""}, "token_file"),
    ({"poll_seconds": 0}, "poll_seconds"),
    ({"id": "XYZ12345"}, "id"),
    ({"repos": {"no-slash": {"labels": [["self-hosted"]]}}}, "no-slash"),
    ({"repos": {"octo/app": {"labels": []}}}, "repos.octo/app.labels"),
    ({"repos": {"octo/app": {"labels": [["self-hosted", ""]]}}}, "repos.octo/app.labels"),
])
def test_an_invalid_machine_config_names_the_key(over, key):
    with pytest.raises(ConfigError) as err:
        garage_config.parse_machine_config(json.dumps(machine(**over)))
    assert key in str(err.value)


def test_text_that_is_not_json_is_a_config_error():
    with pytest.raises(ConfigError, match="not valid JSON"):
        garage_config.parse_machine_config('{"runners": 2,')


def test_a_missing_config_says_where_to_copy_the_example_from(tmp_path):
    with pytest.raises(ConfigError) as err:
        garage_config.load_machine_config(tmp_path / "garage.json")
    assert str(garage_config.EXAMPLE_PATH) in str(err.value)
    assert "copy" in str(err.value)


def test_the_shipped_example_parses_with_no_id_and_no_repos():
    cfg = garage_config.load_machine_config(garage_config.EXAMPLE_PATH)
    assert "id" not in cfg
    assert cfg["repos"] == {}
    assert cfg["runners"] == 3


def test_ensure_id_writes_eight_hex_once(tmp_path):
    path = write(tmp_path, machine(note="kept"))
    assert garage_config.ensure_id(path, token_hex=lambda n: "3f9c2a71"[: n * 2]) == "3f9c2a71"
    assert garage_config.ensure_id(path, token_hex=lambda n: pytest.fail("rewrote the id")) == "3f9c2a71"
    raw = json.loads(path.read_text())
    assert raw["id"] == "3f9c2a71"
    assert raw["note"] == "kept"
    assert "poll_seconds" not in raw  # defaults are never written into the operator's file


def test_update_keeps_the_file_mode_and_refuses_an_invalid_result(tmp_path):
    path = write(tmp_path, machine())
    path.chmod(0o640)
    garage_config.set_repo(path, "octo/app", [["self-hosted", "linux"]])
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    before = path.read_text()
    with pytest.raises(ConfigError):
        garage_config.update_machine_config(path, lambda raw: raw.__setitem__("runners", 0))
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["garage.json"]  # no temp file left behind


def test_set_and_remove_repo(tmp_path):
    path = write(tmp_path, machine())
    garage_config.set_repo(path, "octo/site", [["self-hosted", "linux"], ["self-hosted", "large"]])
    assert json.loads(path.read_text())["repos"] == {
        "octo/site": {"labels": [["self-hosted", "linux"], ["self-hosted", "large"]]}}
    assert garage_config.remove_repo(path, "octo/site") is True
    assert garage_config.remove_repo(path, "octo/site") is False
    assert json.loads(path.read_text())["repos"] == {}


# --- repo runner section ------------------------------------------------------

def test_no_file_and_no_section_are_an_empty_section():
    assert garage_config.parse_runner_section(None) == RunnerSection()
    assert garage_config.parse_runner_section('{"validate": "make test"}') == RunnerSection()


def test_a_valid_section():
    text = json.dumps({"runner": {"env": {"TZ": "UTC", "_X1": "y"}, "setup": "apt-get update"}})
    assert garage_config.parse_runner_section(text) == RunnerSection(
        env={"TZ": "UTC", "_X1": "y"}, setup="apt-get update")


@pytest.mark.parametrize("runner, reason", [
    ([], "runner: must be an object"),
    ({"env": ["TZ"]}, "runner.env"),
    ({"env": {"1X": "a"}}, "not a plain variable name"),
    ({"env": {"MY-VAR": "a"}}, "not a plain variable name"),
    ({"env": {"ACTIONS_RUNNER_INPUT_JITCONFIG": "a"}}, "reserved prefix"),
    ({"env": {"PITCALL_REPO": "a"}}, "reserved prefix"),
    ({"env": {"N": 5}}, "runner.env.N"),
    ({"setup": ""}, "runner.setup"),
    ({"setup": ["apt-get"]}, "runner.setup"),
])
def test_an_invalid_section_is_refused_with_a_reason(runner, reason):
    with pytest.raises(ConfigError) as err:
        garage_config.parse_runner_section(json.dumps({"runner": runner}))
    assert reason in str(err.value)


def test_an_env_value_never_appears_in_the_reason():
    with pytest.raises(ConfigError) as err:
        garage_config.parse_runner_section(json.dumps({"runner": {"env": {"9BAD": "s3cr3t-value"}}}))
    assert "s3cr3t-value" not in str(err.value)


def test_a_repo_config_that_is_not_json_is_refused():
    with pytest.raises(ConfigError, match="not valid JSON"):
        garage_config.parse_runner_section("{")


@pytest.mark.parametrize("name", ["PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PWD",
                                  "OLDPWD", "IFS", "ENV", "CDPATH", "LANG", "LANGUAGE", "SNAP",
                                  "LC_ALL", "LD_PRELOAD", "DOCKER_HOST", "LXD_DIR", "LXC_CONF",
                                  "XDG_RUNTIME_DIR", "SNAP_NAME"])
def test_an_env_name_the_recipe_host_side_reads_is_refused(name):
    text = json.dumps({"runner": {"env": {name: "x"}}})
    with pytest.raises(garage_config.ConfigError) as err:
        garage_config.parse_runner_section(text)
    assert name in str(err.value)
    assert "host" in str(err.value)


@pytest.mark.parametrize("name", ["TZ", "PATHS", "MY_PATH", "HOMEBREW_NO_ANALYTICS", "CI", "LCX",
                                  "DOCKERFILE", "NODE_ENV"])
def test_ordinary_env_names_still_pass(name):
    assert garage_config.parse_runner_section(
        json.dumps({"runner": {"env": {name: "x"}}})).env == {name: "x"}


def test_the_host_name_lists_are_pinned():
    assert garage_config.HOST_NAMES == frozenset({
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PWD", "OLDPWD", "IFS", "ENV",
        "CDPATH", "LANG", "LANGUAGE", "SNAP"})
    assert garage_config.HOST_PREFIXES == ("LC_", "LD_", "DOCKER_", "LXD_", "LXC_", "XDG_",
                                           "SNAP_")
