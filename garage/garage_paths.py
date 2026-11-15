"""Where the garage keeps its files.

XDG base directories, each falling back to its default under HOME when the
variable is unset, empty or not absolute (the XDG spec says a relative value is
invalid and is to be ignored). Every function takes the environment as an
argument so tests never read or write the real home directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(env):
    return os.environ if env is None else env


def _home(env):
    return Path(_env(env).get("HOME") or Path.home())


def _xdg(env, name, default):
    value = _env(env).get(name, "")
    return Path(value) if value and os.path.isabs(value) else _home(env) / default


def config_home(env=None) -> Path:
    return _xdg(env, "XDG_CONFIG_HOME", ".config")


def data_home(env=None) -> Path:
    return _xdg(env, "XDG_DATA_HOME", ".local/share")


def state_home(env=None) -> Path:
    return _xdg(env, "XDG_STATE_HOME", ".local/state")


def machine_config_path(env=None) -> Path:
    return config_home(env) / "pitcall" / "garage.json"


def install_dir(env=None) -> Path:
    return data_home(env) / "pitcall" / "garage"


def state_file_path(env=None) -> Path:
    return state_home(env) / "pitcall" / "garage.json"


def expand(path: str, env=None) -> Path:
    """`~` or a leading `~/` becomes HOME; anything else is returned as given."""
    if path == "~":
        return _home(env)
    if path.startswith("~/"):
        return _home(env) / path[2:]
    return Path(path)
