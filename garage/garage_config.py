"""The garage's two configs: the machine config, and a repo's `runner` section.

The machine config (`~/.config/pitcall/garage.json`) is the operator's file.
`garage start` writes its `id` and `garage join`/`leave` its `repos`; every
other key is kept exactly as the operator wrote it, which is why edits go
through `update_machine_config` on the raw JSON rather than re-serialising the
validated copy (that one has defaults filled in).

A repo's `runner` section comes from that repo's `.pitcall/config.json` on its
default branch (decision 16). Error messages never quote an `env` value.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class ConfigError(Exception):
    """A config the garage cannot use; the message is the operator-facing reason."""


DEFAULTS = {"idle_timeout_minutes": 10, "poll_seconds": 15, "pickup_deadline_minutes": 5}
RECIPE_COMMANDS = ("start", "stop", "exec", "cleanup")
EXAMPLE_PATH = Path(__file__).resolve().parent / "garage.example.json"

#: `owner/name`; a segment of dots alone (`..`, `.`) would walk the API path, so it is refused.
REPO_RE = re.compile(r"(?!\.+/)[A-Za-z0-9_.-]+/(?!\.+\Z)[A-Za-z0-9_.-]+\Z")
ID_RE = re.compile(r"[0-9a-f]{8}\Z")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
RESERVED_PREFIXES = ("ACTIONS_RUNNER_", "PITCALL_")
#: Names the recipe's host side reads. The service hands each repo `env` value to the recipe
#: under its own name, so a repo setting one of these would steer the host-side `docker` or
#: `lxc` call instead of its job.
HOST_NAMES = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "PWD", "OLDPWD",
                        "IFS", "ENV", "CDPATH", "LANG", "LANGUAGE", "SNAP"})
HOST_PREFIXES = ("LC_", "LD_", "DOCKER_", "LXD_", "LXC_", "XDG_", "SNAP_")
#: What no process environment or command line can carry, so Popen refuses it: a NUL, or a lone
#: surrogate (JSON's "\ud800" reads back as one).
UNPASSABLE = re.compile("[\x00\ud800-\udfff]")


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _label_sets_ok(sets) -> bool:
    return isinstance(sets, list) and bool(sets) and all(
        isinstance(s, list) and s and all(isinstance(label, str) and label.strip() for label in s)
        for s in sets)


def parse_machine_config(text: str) -> dict:
    try:
        raw = json.loads(text)
    except RecursionError:
        raise ConfigError("the garage config is nested too deeply to read") from None
    except ValueError as exc:  # JSONDecodeError, and the integer-digits limit
        raise ConfigError(f"the garage config is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError("the garage config must be a JSON object")
    if not _positive_int(raw.get("runners")):
        raise ConfigError("runners: must be a whole number of at least 1")
    recipe = raw.get("recipe")
    if not isinstance(recipe, dict):
        raise ConfigError("recipe: must be an object with start, stop, exec and cleanup")
    for name in RECIPE_COMMANDS:
        if not isinstance(recipe.get(name), str) or not recipe[name].strip():
            raise ConfigError(f"recipe.{name}: must be a non-empty command")
    token_file = raw.get("token_file")
    if not isinstance(token_file, str) or not token_file.strip():
        raise ConfigError("token_file: must name the file holding the token")
    for key in DEFAULTS:
        if key in raw and not _positive_int(raw[key]):
            raise ConfigError(f"{key}: must be a whole number of at least 1")
    if "id" in raw and not (isinstance(raw["id"], str) and ID_RE.match(raw["id"])):
        raise ConfigError("id: must be 8 lowercase hex characters; garage start writes it")
    repos = raw.get("repos", {})
    if not isinstance(repos, dict):
        raise ConfigError("repos: must be an object")
    for repo, entry in repos.items():
        if not REPO_RE.match(repo):
            raise ConfigError(f"repos: {repo!r} is not owner/name")
        if not (isinstance(entry, dict) and _label_sets_ok(entry.get("labels"))):
            raise ConfigError(f"repos.{repo}.labels: must be a list of non-empty label lists")
    return {**DEFAULTS, **raw, "repos": repos}


def load_machine_config(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"no garage config at {path}: copy {EXAMPLE_PATH} to {path} and edit it")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ConfigError(f"the garage config at {path} is not UTF-8") from None
    except OSError as exc:
        raise ConfigError(f"cannot read the garage config at {path}: "
                          f"{exc.strerror or exc}") from None
    return parse_machine_config(text)


def update_machine_config(path: Path, mutate: Callable[[dict], None]) -> dict:
    load_machine_config(path)  # refuse a missing or broken file before locking or rewriting
    path = Path(path).resolve()  # a symlinked config is written through, not replaced
    # Lock the directory, not the file: os.replace swaps the file, so a lock on it would not keep
    # the next writer out. A `join`, a `leave` and a `start` at once then apply one by one.
    lock = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = json.loads(path.read_text(encoding="utf-8"))
        mutate(raw)
        text = json.dumps(raw, indent=2) + "\n"
        parse_machine_config(text)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".garage-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    finally:
        os.close(lock)  # closing the descriptor releases the lock
    return raw


def ensure_id(path: Path, token_hex=secrets.token_hex) -> str:
    existing = load_machine_config(path).get("id")
    if existing:
        return existing
    new = token_hex(4)
    update_machine_config(path, lambda raw: raw.__setitem__("id", new))
    return new


def set_repo(path: Path, repo: str, label_sets: list[list[str]]) -> None:
    def mutate(raw):
        raw.setdefault("repos", {})[repo] = {"labels": [list(s) for s in label_sets]}
    update_machine_config(path, mutate)


def remove_repo(path: Path, repo: str) -> bool:
    if repo not in load_machine_config(path)["repos"]:
        return False
    update_machine_config(path, lambda raw: raw["repos"].pop(repo))
    return True


@dataclass(frozen=True)
class RunnerSection:
    env: dict[str, str] = field(default_factory=dict)
    setup: str | None = None


def parse_runner_section(text: str | None) -> RunnerSection:
    """The `runner` section of a repo's `.pitcall/config.json`; `None` text = no file."""
    if text is None:
        return RunnerSection()
    try:
        cfg = json.loads(text)
    except RecursionError:
        raise ConfigError(".pitcall/config.json is nested too deeply to read") from None
    except ValueError as exc:  # JSONDecodeError, and the integer-digits limit
        raise ConfigError(f".pitcall/config.json is not valid JSON: {exc}") from None
    if not isinstance(cfg, dict):
        raise ConfigError(".pitcall/config.json must be a JSON object")
    if "runner" not in cfg:
        return RunnerSection()
    section = cfg["runner"]
    if not isinstance(section, dict):
        raise ConfigError("runner: must be an object")
    env = section.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError("runner.env: must be an object of string values")
    for name, value in env.items():
        if not ENV_NAME_RE.match(name):
            raise ConfigError(f"runner.env: {name!r} is not a plain variable name "
                              "(letters, digits and _, not starting with a digit)")
        if name.startswith(RESERVED_PREFIXES):
            raise ConfigError(f"runner.env: {name} starts with a reserved prefix "
                              "(ACTIONS_RUNNER_ or PITCALL_)")
        if name in HOST_NAMES or name.startswith(HOST_PREFIXES):
            raise ConfigError(f"runner.env: {name} is read by the recipe's host side "
                              "(the garage hands env values to the recipe under their own "
                              "names); set it in the job's workflow instead")
        if not isinstance(value, str):
            raise ConfigError(f"runner.env.{name}: must be a string")
        if UNPASSABLE.search(value):  # else every runner handed to the repo would break
            raise ConfigError(f"runner.env.{name}: must not hold a NUL or a lone surrogate")
    setup = section.get("setup")
    if setup is not None and (not isinstance(setup, str) or not setup.strip()):
        raise ConfigError("runner.setup: must be a non-empty command")
    if setup is not None and UNPASSABLE.search(setup):
        raise ConfigError("runner.setup: must not hold a NUL or a lone surrogate")
    return RunnerSection(env=dict(env), setup=setup)
