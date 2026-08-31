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


def issue_from_branch(cfg, branch):
    """`<branch_prefix><issue>-<slug>` -> `"<issue>"`, or `None`. `branch_name`'s inverse.

    FAILS SOFT, ALWAYS, and that is the whole contract. `branch_prefix` is required
    only by `claim` (docs/configuration.md), so a plan branch cut by hand legitimately
    carries no number and `ship` must still ship. Every miss returns `None` for the
    caller to state; raising would refuse to open a PR over a naming convention the
    project never promised to follow.

    ANCHORED AT THE PREFIX, never searched for. `feat/rewrite-12-tables` contains a
    number and is not issue 12, and a `Closes #12` composed from it would close an
    unrelated issue in whatever repository `gh` happens to resolve to. The remainder
    must be digits followed by `-` -- exactly the shape `branch_name` writes, which is
    why the two live side by side: one convention, one definition, both directions.
    """
    prefix = (cfg or {}).get("branch_prefix")
    if not prefix or not branch or not branch.startswith(prefix):
        return None
    match = re.match(r"(\d+)-", branch[len(prefix):])
    return match.group(1) if match else None


def resolve_branch(cfg, issue, cwd=None):
    """Issue #<issue>'s branch, local or on `origin` -- or `None`.

    `git branch --list` matches a pattern against the ref's short name, and a
    remote-tracking ref's short name carries the remote's own name ahead of
    it (`origin/feat/19-x`, never just `feat/19-x`, which is only ever a
    LOCAL ref's short name) -- so a single un-prefixed pattern only ever
    matches a local ref. That is the whole defect this exists to close: a
    branch cut here and never checked out anywhere else reads as "no branch"
    to every other clone, which turns real activity into a false 0.

    Matched by ISSUE NUMBER, not by `branch_name()`'s full reconstruction --
    retitling the issue changes the slug that produces, but never the number
    a real branch was already cut for. `branch_name()` stays the right tool
    for CREATING a branch, where two simultaneous claimants need to agree on
    one deterministic name; this is for finding whichever one already exists.

    Local wins when a local ref and its own remote-tracking counterpart both
    match -- that is one branch seen twice. Two genuinely DIFFERENT branches
    are a refusal: `claim --take` can create that state itself, because
    `branch_name()` rebuilds the name from the issue's CURRENT title, so a
    retitle cuts a second branch beside the first. Guessing between them is
    how a live claim gets read as stale and stolen.
    """
    pattern = f"{require(cfg, 'branch_prefix')}{issue}-*"
    out = run("git", "branch", "-a", "--list", pattern, f"origin/{pattern}",
             cwd=cwd)
    # `*` marks the branch checked out in the CURRENT worktree; `+` marks one
    # checked out in a DIFFERENT linked worktree of the same repo -- which is
    # the normal state here, since every WDD task gets its own worktree.
    # Leaving `+` unstripped turns a real branch into a garbage candidate
    # name (`+ feat/24-x`), which either reads as a second, ambiguous branch
    # next to its own remote-tracking counterpart, or -- unambiguous -- gets
    # returned as-is and blows up the next git command that takes it.
    names = [line.strip().lstrip("*+").strip()
            for line in out.split("\n") if line.strip()]
    if not names:
        return None

    # A local ref and its remote-tracking counterpart are ONE branch seen
    # twice, not two candidates -- strip the remote prefix before judging
    # ambiguity, or every branch that has been pushed reads as ambiguous
    # with itself.
    REMOTE = "remotes/origin/"
    distinct = {n[len(REMOTE):] if n.startswith(REMOTE) else n for n in names}
    if len(distinct) > 1:
        raise TrackerError(
            f"#{issue} matches {len(distinct)} branches: "
            f"{', '.join(sorted(distinct))}. Refusing to guess which holds the "
            f"work: `git branch --list` returns them sorted, so picking the "
            f"first picks alphabetically, and picking the most recent commit "
            f"is a guess too -- an abandoned branch rebased yesterday would "
            f"beat live work committed last week. Delete the abandoned branch, "
            f"or act on the right one by hand."
        )

    local = [n for n in names if not n.startswith("remotes/")]
    return local[0] if local else names[0]


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


def select_candidates(issues, milestone=None):
    """Open issues as `(number, title)`, lowest number first.

    Pure, so the ordering rule is provable without a network -- the same split
    this module already applies to the staleness decision.

    Two filters, and both earn their place:

    `repos/:owner/:repo/issues` returns pull requests as well as issues, and a
    PR is distinguished only by carrying a `pull_request` key. Claiming one
    would cut a branch for a number that is not an issue at all.

    `milestone` is opt-in. Absent, an issue with no milestone is still a
    candidate: at the time this was written ten of this repository's thirteen
    open issues carried none, and they were the oldest -- filtering them out by
    default would starve precisely the work FIFO exists to reach.
    """
    out = []
    for issue in issues:
        if issue.get("pull_request") is not None:
            continue
        if milestone is not None:
            if ((issue.get("milestone") or {}).get("title")) != milestone:
                continue
        out.append((issue["number"], issue["title"]))
    return sorted(out)


def open_issues(repo, milestone=None):
    """Every open issue, oldest number first. See `select_candidates`.

    Paginated rather than limited. `gh issue list` caps at 30 by default and
    returns NEWEST first, so a limit silently drops the oldest issues -- the
    exact end of the queue a FIFO selector exists to serve, and a truncation
    that looks like a complete answer.
    """
    raw = run("gh", "api", f"repos/{repo}/issues?state=open&per_page=100",
              "--paginate", "--jq", ".[] | @json")
    issues = [json.loads(line) for line in raw.split("\n") if line.strip()]
    return select_candidates(issues, milestone)


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


def _main(argv):
    """`tracker.py issue-from-branch BRANCH` -- the one entry point a shell needs.

    `wdd ship` is a shell script, and the branch->issue convention is defined by
    `branch_name`/`issue_from_branch` above. Restating the parse in shell would put
    one convention in two languages with the edge cases in the untested half, so the
    shell asks this instead.

    Prints the number and exits 0; prints NOTHING and STILL exits 0 when there is
    none, including when the config cannot be read at all. A miss is an ordinary
    answer here rather than an error -- `ship` must ship either way, and a non-zero
    exit would make a naming convention able to block a release.
    """
    if len(argv) != 2 or argv[0] != "issue-from-branch":
        sys.stderr.write("usage: tracker.py issue-from-branch BRANCH\n")
        return 2
    try:
        cfg = config()
    except TrackerError:
        return 0
    found = issue_from_branch(cfg, argv[1])
    if found:
        print(found)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
