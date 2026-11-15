"""The garage's state: its vocabulary, the state file, and what `garage status` prints.

The service rewrites the state file atomically on every loop; `garage status`
reads it whether the service runs or not. The file holds no token, no JIT
config and no `env` value — only names, states, reasons and times.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

RUNNER_STATES = ("stopped", "idle", "starting", "setup", "waiting", "busy", "cleaning", "broken")
REPO_STATUSES = ("ok", "blocked", "setup failing", "config invalid")

# [0-9] rather than the digit escape: the private-content gate flags that escape followed by a
# count.
_EXPIRY = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})"
                     r" (UTC|[+-][0-9]{4})\Z")


def write_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp creates the file with mode 0600.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".garage-state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_state(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def days_left(expiration: str | None, now: float) -> int | None:
    """Whole days until the token expires (negative once it has), from GitHub's header value."""
    if not expiration:
        return None
    match = _EXPIRY.match(expiration.strip())
    if not match:
        return None
    zone = "+0000" if match.group(2) == "UTC" else match.group(2)
    when = datetime.strptime(f"{match.group(1)} {zone}", "%Y-%m-%d %H:%M:%S %z")
    return math.floor((when.timestamp() - now) / 86400)


def _runner_order(name: str):
    digits = name[1:]
    return (0, int(digits)) if name.startswith("r") and digits.isdigit() else (1, name)


def _retry(retry_at, now) -> str:
    if not retry_at:
        return ""
    if retry_at <= now:
        return " (retrying now)"
    return f" (retry in {math.ceil((retry_at - now) / 60)} min)"


def _runner_line(name: str, r: dict, now: float) -> str:
    state, repo = r.get("state"), r.get("repo")
    if state == "busy":
        text = f"busy {repo}" + (f" {r['job']}" if r.get("job") else "")
    elif state == "waiting":
        text = f"waiting for pickup {repo}"
    elif state == "broken":
        text = f"broken — {r.get('error') or 'no error recorded'}" + _retry(r.get("retry_at"), now)
    elif state in ("starting", "setup", "cleaning") and repo:
        text = f"{state} {repo}"
    else:
        text = str(state)
    return f"{name}: {text}"


def _repo_line(repo: str, e: dict, now: float) -> str:
    status = e.get("status")
    labels = ", ".join("+".join(labels) for labels in e.get("labels") or []) or "none"
    counts = f"{e.get('queued', 0)} queued, labels {labels}"
    if status == "ok":
        text = f"ok, {counts}"
    else:
        reason = e.get("reason") or "no reason recorded"
        text = f"{status} — {reason}; {counts}" + _retry(e.get("retry_at"), now)
    return f"{repo}: {text}"


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def render_status(service: str, token: str, state: dict | None, now: float) -> str:
    lines = [f"service: {service}"]
    days = days_left(state.get("token_expiration") if state else None, now)
    if days is None:
        lines.append(f"token: {token}")
    elif days < 0:
        lines.append(f"token: {token} — expired")
    else:
        lines.append(f"token: {token} — expires in {days} days")
    if days is not None and days <= 7:
        when = "has expired" if days < 0 else f"expires in {days} days"
        # The service reads the token only when it starts, so a new one needs a `garage start`.
        lines.append(f"WARNING: the garage token {when}: create a new one, put it in the token "
                     "file, then run garage start")
    if state is None:
        lines.append("runners: unknown (the service has not written its state)")
        return "\n".join(lines) + "\n"
    if state.get("config_error"):
        lines.append(f"config: {state['config_error']} "
                     "(the service carries on with the last config it read)")
    paused = state.get("paused_until")
    if paused and paused > now:
        until = time.strftime("%H:%M:%S", time.localtime(paused))
        lines.append(f"paused: rate limited until {until}")
    # A hand-edited or foreign state file can hold anything: an entry that is not an object is
    # named, not read.
    runners = _as_dict(state.get("runners"))
    if not runners:
        lines.append("runners: none")
    for name in sorted(runners, key=_runner_order):
        entry = runners[name]
        lines.append(_runner_line(name, entry, now) if isinstance(entry, dict)
                     else f"{name}: unreadable")
    repos = _as_dict(state.get("repos"))
    if not repos:
        lines.append("repos: none joined")
    for repo in sorted(repos):
        entry = repos[repo]
        lines.append(_repo_line(repo, entry, now) if isinstance(entry, dict)
                     else f"{repo}: unreadable")
    return "\n".join(lines) + "\n"
