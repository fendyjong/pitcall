"""Run a garage recipe's commands with the recipe contract's environment.

A recipe is four shell strings from the machine config (`start`, `stop`, `exec`, `cleanup`).
Each runs as `/bin/sh -c '<command> "$@"' garage-recipe <args>`, as the operator's own user.
Secrets reach a recipe only through its environment: the JIT config and a repo's `env` are named
in `PITCALL_ENV_NAMES` and carried as variables, never as arguments, so no process listing shows
them (decision 15).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass

OUTPUT_TAIL = 4000
RECORD_DIR = "/var/lib/pitcall-garage/setup"


@dataclass
class Result:
    returncode: int
    output: str


def _argv(command: str, args) -> list[str]:
    return ["/bin/sh", "-c", command + ' "$@"', "garage-recipe", *args]


def _quote(text: str) -> str:
    """`text` as one single-quoted shell word."""
    return "'" + text.replace("'", "'\"'\"'") + "'"


class Recipe:
    def __init__(self, commands: dict[str, str], base_env: dict | None = None,
                 popen=subprocess.Popen):
        self.commands = dict(commands)
        self.base_env = dict(os.environ if base_env is None else base_env)
        self.popen = popen

    def _env(self, runner: str, repo: str | None, extra: dict[str, str] | None) -> dict:
        env = dict(self.base_env)
        env["PITCALL_RUNNER"] = runner
        env.pop("PITCALL_REPO", None)
        if repo is not None:
            env["PITCALL_REPO"] = repo
        env.update(extra or {})
        return env

    def run(self, command: str, runner: str, repo: str | None = None,
            args: tuple[str, ...] = (), extra_env: dict[str, str] | None = None) -> Result:
        proc = self.popen(_argv(self.commands[command], args),
                          env=self._env(runner, repo, extra_env), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        text = (out or b"").decode("utf-8", "replace")
        return Result(proc.returncode, text[-OUTPUT_TAIL:])

    def spawn_job(self, runner: str, repo: str, variables: dict[str, str]):
        env = self._env(runner, repo, variables)
        env["PITCALL_ENV_NAMES"] = " ".join(variables)
        # stdout/stderr None: the job's output goes to the service's own log. A pipe nobody
        # drains would block a job that prints more than the pipe holds.
        return self.popen(_argv(self.commands["exec"], ("job",)), env=env,
                          stdin=subprocess.DEVNULL, stdout=None, stderr=None)


def setup_script(repo: str, setup: str, record_dir: str = RECORD_DIR) -> str:
    """The shell text passed as PITCALL_SETUP: run `setup` unless this runner already ran this text.

    The record lives inside the runner, so a recreated container runs `setup` again.
    """
    digest = hashlib.sha256(setup.encode("utf-8")).hexdigest()
    record = f"{record_dir}/{repo.replace('/', '__')}.sha256"
    return "\n".join([
        "set -e",
        f"record={_quote(record)}",
        f'if [ "$(cat "$record" 2>/dev/null)" = {digest} ]; then exit 0; fi',
        f"sh -c {_quote(setup)}",
        f"mkdir -p {_quote(record_dir)}",
        f'printf "%s\\n" {digest} > "$record"',
        "",
    ])
