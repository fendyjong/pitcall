"""Shared mechanism for the issue verbs: config, slug, staleness, fetchers.

`claim` and `file` are thin CLIs over this module. The split is a testability
constraint rather than tidiness: the staleness decision has to be provable in
both directions, and a decision tangled with its own fetching can only be
exercised against a live GitHub repository.

Named `.py` and importable because that is what root `scripts/` already is --
`lane.py` and `lane_config.py` are imported directly by `tests/`. The skill's
own `scripts/` are extensionless CLIs run as subprocesses; this is the other
convention, and the one the pure-function requirement needs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lane_config  # noqa: E402

#: Every claim comment starts with this. It is the address: comment position is
#: not, because comments accumulate and `--take` deliberately adds another.
CLAIM_MARKER = "claimed by "


class TrackerError(RuntimeError):
    """Stops a verb with a message, never a traceback."""


def _require_aware(value, name):
    """`value`, or a `TrackerError` naming `name` when it is a naive datetime.

    `datetime.now()` is naive by default -- the single most natural way a
    caller produces `now=` -- while every timestamp this module actually
    handles is aware (`%cI` from git, ISO8601 from the GitHub API). Silently
    coercing a naive value to UTC would guess at a timezone the caller did
    not state, and guessing wrong shifts a claim's age by hours in a rule
    whose whole job is deciding whether 24 of them have passed. So this
    refuses instead of guessing.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise TrackerError(
            f"{name} must be a timezone-aware datetime, not a naive one -- "
            f"this module never guesses at a timezone."
        )
    return value


def run(*args, cwd=None):
    """stdout, or a loud failure naming the command.

    A claim that half-happened is the state the comment-before-branch ordering
    exists to avoid, so nothing here degrades to a warning.
    """
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise TrackerError(
            f"{' '.join(args)} failed: {result.stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def config(checkout=None):
    """The project's pitcall config, or a `TrackerError` when it cannot be read.

    `lane_config.load_config` raises a bare `RuntimeError` on a missing
    checkout or a missing required key. `TrackerError` subclasses
    `RuntimeError`, so that subclass relationship runs the wrong way for a
    caller: `except TrackerError` does not catch a bare `RuntimeError`. This
    is where that boundary is declared -- the same reader, its error
    translated, not a second config reader.
    """
    try:
        return lane_config.load_config(checkout)
    except RuntimeError as exc:
        raise TrackerError(str(exc)) from exc


def require(cfg, key):
    value = cfg.get(key)
    if not value:
        raise TrackerError(
            f"{key} is not set in this project's pitcall config. This verb "
            f"cannot guess it -- add the key and run again."
        )
    return value


def status_label(cfg, name):
    """The project's label for `name`, or None when the key is absent.

    Absent is a legitimate answer: `status_labels` is optional, so a project
    that never set it gets the rest of the verb with the label half skipped and
    said out loud. Never invent a name.
    """
    return (cfg.get("status_labels") or {}).get(name) or None


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slug(title, limit=40):
    """A deterministic branch slug, so two sessions agree on the branch name."""
    return _NON_SLUG.sub("-", title.lower()).strip("-")[:limit].strip("-")


def branch_name(cfg, issue, title):
    """`<branch_prefix><issue>-<slug>`.

    `branch_prefix` carries its own separator (`feat/`, `wdd/`), so this
    concatenates. Joining with "/" yields `feat//19-...`, which git accepts and
    nobody means.
    """
    return f"{require(cfg, 'branch_prefix')}{issue}-{slug(title)}"


def is_stale(comment_time, commits_since, claim_expiry_hours, now):
    """"live" or "stale", from data alone -- no network, no git.

    BOTH halves are required. Age alone steals a task legitimately running
    long; commit-recency alone never expires a branch whose session died after
    one commit. This is a pure function so both directions are provable
    offline.

    `comment_time` and `now` must both be timezone-aware -- see
    `_require_aware`. Never pass a naive `datetime.now()` here.
    """
    _require_aware(comment_time, "comment_time")
    _require_aware(now, "now")
    expired = now - comment_time >= timedelta(hours=float(claim_expiry_hours))
    return "stale" if expired and commits_since == 0 else "live"


def origin_repo(cwd=None):
    """`owner/name` for the checkout's origin -- the repo the verbs act on."""
    return run("gh", "repo", "view", "--json", "nameWithOwner",
               "-q", ".nameWithOwner", cwd=cwd).strip()


def _comments(issue, repo):
    raw = run("gh", "api", f"repos/{repo}/issues/{issue}/comments",
              "--paginate", "--jq", ".[] | @json")
    return [json.loads(line) for line in raw.split("\n") if line.strip()]


def latest_claim(issue, repo):
    """The most recent comment whose body starts with CLAIM_MARKER, or None.

    Reversed rather than sorted: the API returns comments in creation order, and
    `--take` appends, so the last match is the live claim and earlier ones are
    superseded records.
    """
    for comment in reversed(_comments(issue, repo)):
        if comment["body"].startswith(CLAIM_MARKER):
            return comment
    return None


def commits_since(branch, base, since, cwd=None):
    """Commits on `branch` past `base`, committed at or after `since`.

    `base..branch`, never `git log branch`: a freshly cut branch inherits the
    whole base history, every commit of which predates any claim. Committer
    date, never author date -- a rebase preserves the author date, so an
    author-date filter reads replayed work as old and expires a live claim.

    `since` must be timezone-aware -- see `_require_aware`.
    """
    _require_aware(since, "since")
    out = run("git", "log", f"{base}..{branch}", "--format=%cI", cwd=cwd)
    return sum(
        1 for line in out.split("\n")
        if line.strip() and datetime.fromisoformat(line.strip()) >= since
    )
