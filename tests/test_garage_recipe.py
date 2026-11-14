"""The recipe runner and the shipped example recipes (Task 7).

Nothing here starts a container. The runner is tested with a fake Popen and with tiny real shell
scripts; the shipped recipes are run against fake `docker` and `lxc` commands that only record
what they were given.
"""

import hashlib
import os
import signal
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "garage"))
import garage_recipe  # noqa: E402

RECIPES = ROOT / "garage" / "recipes"
COMMANDS = ("start", "stop", "exec", "cleanup")
SECRET = "jit-secret-value"
JIT = "ACTIONS_RUNNER_INPUT_JITCONFIG"


def recorder(code=0, output=b"", timeouts=0):
    """A fake Popen class and the list of every process it 'started'.

    Its first `timeouts` calls to communicate() run out of time, as a hung command's would.
    """
    made = []

    class FakePopen:
        pid = 4242

        def __init__(self, argv, env=None, **kwargs):
            self.argv, self.env, self.kwargs, self.returncode = argv, env, kwargs, code
            self.waits, self.timeouts = [], timeouts
            made.append(self)

        def communicate(self, *args, timeout=None, **kwargs):
            self.waits.append(timeout)
            if self.timeouts:
                self.timeouts -= 1
                raise subprocess.TimeoutExpired(self.argv, timeout, output=output)
            return output, None

    return FakePopen, made


def recipe(popen, base_env=None, **kwargs):
    commands = {c: f"/r/{c}.sh" for c in COMMANDS}
    return garage_recipe.Recipe(commands, base_env=base_env or {"HOME": "/home/op"}, popen=popen,
                                **kwargs)


# --- the runner, against a fake Popen ------------------------------------------------------------


def test_a_command_runs_under_sh_with_its_arguments():
    popen, made = recorder()
    recipe(popen).run("exec", "r1", "octo/app", args=("setup",))
    assert made[0].argv == ["/bin/sh", "-c", '/r/exec.sh "$@"', "garage-recipe", "setup"]


def test_a_command_gets_the_runner_the_repo_and_the_base_environment():
    popen, made = recorder()
    recipe(popen).run("start", "r2", "octo/app")
    env = made[0].env
    assert env["PITCALL_RUNNER"] == "r2"
    assert env["PITCALL_REPO"] == "octo/app"
    assert env["HOME"] == "/home/op"


def test_a_command_for_no_repo_gets_no_repo_variable():
    popen, made = recorder()
    recipe(popen, base_env={"PITCALL_REPO": "stale/repo"}).run("cleanup", "r1")
    assert "PITCALL_REPO" not in made[0].env


def test_extra_variables_reach_the_environment_and_not_the_command_line():
    popen, made = recorder()
    recipe(popen).run("exec", "r1", "octo/app", args=("setup",),
                      extra_env={"PITCALL_SETUP": "echo the-setup-text"})
    assert made[0].env["PITCALL_SETUP"] == "echo the-setup-text"
    assert not any("the-setup-text" in arg for arg in made[0].argv)


def test_the_result_carries_the_exit_status_and_the_end_of_the_output():
    popen, _ = recorder(code=3, output=b"x" * 5000 + b"tail")
    result = recipe(popen).run("start", "r1")
    assert result.returncode == 3
    assert len(result.output) == 4000
    assert result.output.endswith("tail")


def test_a_command_gets_an_hour_by_default():
    popen, made = recorder()
    recipe(popen).run("start", "r1")
    assert made[0].waits == [garage_recipe.TIMEOUT_SECONDS] == [3600]


def test_a_command_that_runs_too_long_is_killed_with_its_whole_group():
    killed = []
    popen, made = recorder(output=b"x" * 5000, timeouts=1)
    result = recipe(popen, killpg=lambda pid, sig: killed.append((pid, sig))).run(
        "start", "r1", timeout=5)
    assert made[0].kwargs.get("start_new_session") is True
    assert killed == [(4242, signal.SIGKILL)]
    assert result.returncode == 124
    assert len(result.output) == 4000
    assert result.output.endswith("x\n[garage: timed out after 5 s]")


def test_a_killed_command_whose_output_stays_open_still_returns():
    popen, made = recorder(output=b"partial", timeouts=2)
    result = recipe(popen, killpg=lambda pid, sig: None).run("start", "r1", timeout=5)
    assert result.returncode == 124
    assert result.output == "partial\n[garage: timed out after 5 s]"
    assert len(made[0].waits) == 2 and made[0].waits[1] is not None


def test_a_job_carries_its_variables_in_the_environment_and_never_in_argv():
    popen, made = recorder()
    recipe(popen).spawn_job("r1", "octo/app", {JIT: SECRET, "TZ": "UTC"})
    proc = made[0]
    assert proc.argv == ["/bin/sh", "-c", '/r/exec.sh "$@"', "garage-recipe", "job"]
    assert not any(SECRET in arg for arg in proc.argv)
    assert proc.env[JIT] == SECRET
    assert proc.env["TZ"] == "UTC"
    assert proc.env["PITCALL_ENV_NAMES"] == f"{JIT} TZ"
    assert proc.env["PITCALL_RUNNER"] == "r1"
    assert proc.env["PITCALL_REPO"] == "octo/app"


def test_a_job_writes_to_the_services_own_output_not_a_pipe():
    popen, made = recorder()
    recipe(popen).spawn_job("r1", "octo/app", {})
    assert made[0].kwargs.get("stdout") is None
    assert made[0].kwargs.get("stderr") is None


def test_a_job_runs_in_its_own_session_so_it_can_be_ended_with_its_group():
    popen, made = recorder()
    recipe(popen).spawn_job("r1", "octo/app", {})
    assert made[0].kwargs.get("start_new_session") is True


# --- the runner, against tiny real scripts --------------------------------------------------------


def write_script(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def test_arguments_and_variables_reach_a_real_script(tmp_path):
    out = tmp_path / "out"
    script = write_script(tmp_path / "rec.sh",
                          'printf "%s|%s|%s|%s\\n" "${1:-}" "$PITCALL_RUNNER" '
                          '"${PITCALL_REPO:-none}" "${PITCALL_SETUP:-}" >> "$OUT"\n')
    r = garage_recipe.Recipe({c: str(script) for c in COMMANDS},
                             base_env={"OUT": str(out), "PATH": os.environ["PATH"]})
    assert r.run("exec", "r2", "octo/app", ("setup",), {"PITCALL_SETUP": "it's set"}).returncode == 0
    assert r.run("cleanup", "r2").returncode == 0
    assert out.read_text().splitlines() == ["setup|r2|octo/app|it's set", "|r2|none|"]


def test_a_real_script_failing_is_reported_with_its_output(tmp_path):
    script = write_script(tmp_path / "fail.sh", 'echo "no docker here" >&2\nexit 4\n')
    r = garage_recipe.Recipe({c: str(script) for c in COMMANDS}, base_env={"PATH": os.environ["PATH"]})
    result = r.run("start", "r1")
    assert result.returncode == 4
    assert "no docker here" in result.output


def test_a_real_job_sees_each_named_variable(tmp_path):
    out = tmp_path / "out"
    script = write_script(tmp_path / "job.sh",
                          'for n in $PITCALL_ENV_NAMES; do eval "v=\\${$n}"; '
                          'printf "%s=%s\\n" "$n" "$v"; done > "$OUT"\n')
    r = garage_recipe.Recipe({c: str(script) for c in COMMANDS},
                             base_env={"OUT": str(out), "PATH": os.environ["PATH"]})
    proc = r.spawn_job("r1", "octo/app", {JIT: SECRET, "TZ": "UTC"})
    assert proc.wait() == 0
    assert out.read_text().splitlines() == [f"{JIT}={SECRET}", "TZ=UTC"]


# --- the setup script ----------------------------------------------------------------------------


def run_setup(script, cwd):
    return subprocess.run(["/bin/sh", "-c", script], cwd=cwd, capture_output=True, text=True)


def test_setup_runs_once_for_the_same_text(tmp_path):
    records = tmp_path / "records"
    setup = f'echo ran >> "{tmp_path}/count"'
    script = garage_recipe.setup_script("octo/app", setup, record_dir=str(records))
    assert run_setup(script, tmp_path).returncode == 0
    assert run_setup(script, tmp_path).returncode == 0
    assert (tmp_path / "count").read_text() == "ran\n"
    record = records / "octo__app.sha256"
    assert record.read_text().strip() == hashlib.sha256(setup.encode()).hexdigest()


def test_a_changed_setup_runs_again(tmp_path):
    records = str(tmp_path / "records")
    for word in ("one", "two"):
        setup = f'echo {word} >> "{tmp_path}/count"'
        assert run_setup(garage_recipe.setup_script("octo/app", setup, record_dir=records),
                         tmp_path).returncode == 0
    assert (tmp_path / "count").read_text() == "one\ntwo\n"


def test_a_failed_setup_fails_leaves_no_record_and_runs_again(tmp_path):
    records = tmp_path / "records"
    setup = f'echo tried >> "{tmp_path}/count"; exit 3'
    script = garage_recipe.setup_script("octo/app", setup, record_dir=str(records))
    assert run_setup(script, tmp_path).returncode != 0
    assert not (records / "octo__app.sha256").exists()
    assert run_setup(script, tmp_path).returncode != 0
    assert (tmp_path / "count").read_text() == "tried\ntried\n"


def test_a_setup_with_quotes_runs_as_written(tmp_path):
    setup = f"printf '%s' \"it's quoted\" > '{tmp_path}/out'"
    script = garage_recipe.setup_script("octo/app", setup, record_dir=str(tmp_path / "records"))
    assert run_setup(script, tmp_path).returncode == 0
    assert (tmp_path / "out").read_text() == "it's quoted"


def test_each_repo_has_its_own_record(tmp_path):
    records = str(tmp_path / "records")
    setup = f'echo ran >> "{tmp_path}/count"'
    for repo in ("octo/app", "octo/site"):
        assert run_setup(garage_recipe.setup_script(repo, setup, record_dir=records),
                         tmp_path).returncode == 0
    assert (tmp_path / "count").read_text() == "ran\nran\n"


# --- the shipped recipes -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["docker", "lxd"])
def test_a_recipe_ships_four_executable_scripts_and_a_readme(kind):
    directory = RECIPES / kind
    assert (directory / "README.md").is_file()
    for command in COMMANDS:
        script = directory / f"{command}.sh"
        assert script.read_text().startswith("#!/bin/sh\n"), script
        assert script.stat().st_mode & stat.S_IXUSR, f"{script} is not executable"


@pytest.mark.parametrize("script", sorted(RECIPES.glob("*/*.sh")),
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_recipe_script_parses(script):
    assert subprocess.run(["/bin/sh", "-n", str(script)]).returncode == 0


FAKE_TOOL = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE_LOG"
printf -- '--\\n' >> "$FAKE_LOG"
printf '%s\\n' "${ACTIONS_RUNNER_INPUT_JITCONFIG:-}" >> "$FAKE_ENV"
[ -z "${FAKE_DUMP:-}" ] || env > "$FAKE_DUMP"
case "$*" in
  "container inspect -f"*) echo "${FAKE_RUNNING:-false}"; exit "${FAKE_MISSING:-0}" ;;
  "container inspect"*) exit "${FAKE_MISSING:-0}" ;;
  "info "*)
    # A real lxc translates its output unless the locale is C.
    if [ "${LC_ALL:-}" = C ]; then label="Status:"; else label="Statut :"; fi
    [ -z "${FAKE_STATUS:-}" ] || echo "$label $FAKE_STATUS"
    exit "${FAKE_MISSING:-0}" ;;
  "stop --timeout"*) exit "${FAKE_STOP_FAILS:-0}" ;;
esac
cat >> "$FAKE_STDIN"
"""


def fake_tools(tmp_path, **variables):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("docker", "lxc"):
        (bin_dir / tool).write_text(FAKE_TOOL)
        (bin_dir / tool).chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_LOG": str(tmp_path / "argv"),
            "FAKE_ENV": str(tmp_path / "env"), "FAKE_STDIN": str(tmp_path / "stdin"),
            "PITCALL_RUNNER": "r1", **variables}


def run_recipe(kind, command, args, env):
    return subprocess.run(["/bin/sh", str(RECIPES / kind / f"{command}.sh"), *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)


def logged_argv(tmp_path):
    return (tmp_path / "argv").read_text().splitlines()


def recorded_calls(tmp_path):
    return [c for c in (tmp_path / "argv").read_text().split("--\n") if c]


def test_the_docker_job_names_its_variables_and_never_their_values(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_ENV_NAMES=f"{JIT} TZ",
                     ACTIONS_RUNNER_INPUT_JITCONFIG=SECRET, TZ="UTC")
    result = run_recipe("docker", "exec", ["job"], env)
    assert result.returncode == 0, result.stderr
    argv = logged_argv(tmp_path)
    assert SECRET not in "\n".join(argv)
    assert argv[0] == "exec"
    for name in (JIT, "TZ"):
        assert argv[argv.index(name) - 1] == "-e"
    assert "pitcall-garage-r1" in argv
    assert "./run.sh" in argv
    # `-e NAME` works only because docker itself received the value in its environment.
    assert SECRET in (tmp_path / "env").read_text()


def test_the_docker_setup_passes_its_script_by_name(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_SETUP="echo the-setup-text")
    result = run_recipe("docker", "exec", ["setup"], env)
    assert result.returncode == 0, result.stderr
    argv = logged_argv(tmp_path)
    assert "the-setup-text" not in "\n".join(argv)
    assert argv[argv.index("PITCALL_SETUP") - 1] == "-e"


def test_the_lxd_job_sends_its_variables_on_stdin(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_ENV_NAMES=f"{JIT} TZ",
                     ACTIONS_RUNNER_INPUT_JITCONFIG=SECRET, TZ="it's UTC")
    result = run_recipe("lxd", "exec", ["job"], env)
    assert result.returncode == 0, result.stderr
    argv = "\n".join(logged_argv(tmp_path))
    assert SECRET not in argv and "it's UTC" not in argv
    # As root, then runuser: it gives `runner` its groups, `docker` among them; --user would not.
    assert "--\nrunuser\n-u\nrunner\n--\nsh\n-s" in argv and "--user" not in argv
    lines = (tmp_path / "stdin").read_text().splitlines()
    assert lines[-1] == "exec ./run.sh"
    exports = tmp_path / "exports.sh"
    exports.write_text("\n".join(lines[:-1]) + "\n")
    shown = subprocess.run(["/bin/sh", "-c", f'. "$1"; printf "%s|%s" "${JIT}" "$TZ"', "sh",
                            str(exports)], capture_output=True, text=True).stdout
    assert shown == f"{SECRET}|it's UTC"


def test_the_lxd_setup_sends_its_script_on_stdin(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_SETUP="echo the-setup-text")
    result = run_recipe("lxd", "exec", ["setup"], env)
    assert result.returncode == 0, result.stderr
    assert "the-setup-text" not in "\n".join(logged_argv(tmp_path))
    assert (tmp_path / "stdin").read_text() == "echo the-setup-text"


@pytest.mark.parametrize("kind", ["docker", "lxd"])
def test_cleanup_and_stop_succeed_on_a_missing_container_and_only_look(tmp_path, kind):
    env = fake_tools(tmp_path, FAKE_MISSING="1")
    for command in ("cleanup", "stop"):
        result = run_recipe(kind, command, [], env)
        assert result.returncode == 0, (command, result.stderr)
    calls = recorded_calls(tmp_path)
    assert calls and all(c.startswith(("container\ninspect\n", "info\n")) for c in calls)


def test_the_lxd_start_leaves_a_running_container_running(tmp_path):
    env = fake_tools(tmp_path, FAKE_STATUS="RUNNING", LC_ALL="xx_XX.UTF-8")
    result = run_recipe("lxd", "start", [], env)
    assert result.returncode == 0, result.stderr
    assert "start\npitcall-garage-r1\n" not in recorded_calls(tmp_path)


def test_the_lxd_stop_forces_a_container_that_does_not_stop_in_time(tmp_path):
    env = fake_tools(tmp_path, FAKE_STATUS="RUNNING", FAKE_STOP_FAILS="1", LC_ALL="xx_XX.UTF-8")
    result = run_recipe("lxd", "stop", [], env)
    assert result.returncode == 0, result.stderr
    assert recorded_calls(tmp_path)[-2:] == ["stop\n--timeout\n30\npitcall-garage-r1\n",
                                             "stop\n--force\npitcall-garage-r1\n"]


@pytest.mark.parametrize("kind", ["docker", "lxd"])
def test_exec_refuses_an_unknown_mode(tmp_path, kind):
    assert run_recipe(kind, "exec", ["dance"], fake_tools(tmp_path)).returncode == 2


def heredoc(script):
    """The body of the one `<<'EOF'` script a recipe file runs inside its container."""
    text = script.read_text()
    assert text.count("<<'EOF'\n") == 1, script
    start = text.index("<<'EOF'\n") + len("<<'EOF'\n")
    return text[start:text.index("\nEOF\n", start) + 1]


@pytest.mark.parametrize("command", ["start", "cleanup"])
def test_both_recipes_run_the_same_script_inside_their_container(command):
    # Each recipe directory is copied and edited on its own, so the copies stay; this keeps a fix
    # to one from missing the other.
    docker, lxd = (RECIPES / kind / f"{command}.sh" for kind in ("docker", "lxd"))
    assert heredoc(docker) == heredoc(lxd), (
        f"{docker.relative_to(ROOT)} and {lxd.relative_to(ROOT)} differ inside their <<'EOF' script")


# --- sweep (#59) -------------------------------------------------------------------------------


def test_the_lxd_job_keeps_a_values_trailing_newlines(tmp_path):
    value = "two lines\nand a blank one\n\n"
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_ENV_NAMES="NOTE", NOTE=value)
    result = run_recipe("lxd", "exec", ["job"], env)
    assert result.returncode == 0, result.stderr
    exports = (tmp_path / "stdin").read_text().removesuffix("exec ./run.sh\n")
    shown = subprocess.run(["/bin/sh", "-c", exports + 'printf "%s|" "$NOTE"'],
                           capture_output=True, text=True).stdout
    assert shown == value + "|"


def test_the_docker_start_creates_a_missing_runner_with_its_own_network_and_docker(tmp_path):
    env = fake_tools(tmp_path, FAKE_MISSING="1")
    result = run_recipe("docker", "start", [], env)
    assert result.returncode == 0, result.stderr
    calls = recorded_calls(tmp_path)
    created = ("run\n-d\n--name\npitcall-garage-r1\n--hostname\npitcall-garage-r1\n"
               "--network\npitcall-garage-r1\n--privileged\n--init\n"
               "-v\npitcall-garage-r1-docker:/var/lib/docker\nubuntu:24.04\nsleep\ninfinity\n")
    assert created in calls
    assert "start\npitcall-garage-r1\n" in calls
    assert calls[-2:] == ["exec\n-u\n0\npitcall-garage-r1\npgrep\n-x\ndockerd\n",
                          "exec\n-u\n0\npitcall-garage-r1\ndocker\ninfo\n"]
    assert (tmp_path / "stdin").read_text() == heredoc(RECIPES / "docker" / "start.sh")


def test_the_docker_start_leaves_a_running_container_running(tmp_path):
    env = fake_tools(tmp_path, FAKE_RUNNING="true")
    result = run_recipe("docker", "start", [], env)
    assert result.returncode == 0, result.stderr
    calls = recorded_calls(tmp_path)
    assert "start\npitcall-garage-r1\n" not in calls
    assert not any(c.startswith("run\n") for c in calls)


@pytest.mark.parametrize("kind", ["docker", "lxd"])
def test_cleanup_on_an_existing_runner_starts_it_then_cleans_inside_it(tmp_path, kind):
    env = fake_tools(tmp_path, FAKE_STATUS="RUNNING", FAKE_RUNNING="true")
    result = run_recipe(kind, "cleanup", [], env)
    assert result.returncode == 0, result.stderr
    # start.sh's install script first (it is idempotent), then the clean, both on stdin.
    assert (tmp_path / "stdin").read_text() == (heredoc(RECIPES / kind / "start.sh")
                                               + heredoc(RECIPES / kind / "cleanup.sh"))


@pytest.mark.parametrize("kind", ["docker", "lxd"])
def test_a_recipe_readme_says_a_later_job_can_read_or_tamper_with_what_is_left(kind):
    text = " ".join((RECIPES / kind / "README.md").read_text().split())
    assert ("a job from one repo can read, or tamper with, what an earlier job from another repo "
            "left in them") in text


#: Repo env names that are also the names the exec scripts used for their own shell variables.
CLASHING = {"value": "repo-value", "var": "repo-var", "name": "repo-name", "TZ": "UTC"}


def test_the_lxd_job_carries_a_repo_value_named_like_a_script_variable_intact(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_ENV_NAMES=" ".join(CLASHING),
                     **CLASHING)
    result = run_recipe("lxd", "exec", ["job"], env)
    assert result.returncode == 0, result.stderr
    exports = (tmp_path / "stdin").read_text().removesuffix("exec ./run.sh\n")
    shown = subprocess.run(["/bin/sh", "-c", exports + 'printf "%s|%s|%s|%s" "$value" "$var" '
                            '"$name" "$TZ"'], capture_output=True, text=True).stdout
    assert shown == "repo-value|repo-var|repo-name|UTC"
    assert "pitcall-garage-r1" in logged_argv(tmp_path)


def test_the_docker_job_carries_a_repo_value_named_like_a_script_variable_intact(tmp_path):
    env = fake_tools(tmp_path, PITCALL_REPO="octo/app", PITCALL_ENV_NAMES=" ".join(CLASHING),
                     FAKE_DUMP=str(tmp_path / "dump"), **CLASHING)
    result = run_recipe("docker", "exec", ["job"], env)
    assert result.returncode == 0, result.stderr
    seen = dict(line.split("=", 1) for line in (tmp_path / "dump").read_text().splitlines()
                if "=" in line)
    assert {key: seen.get(key) for key in CLASHING} == CLASHING  # what `docker exec -e` reads
    assert "pitcall-garage-r1" in logged_argv(tmp_path)
