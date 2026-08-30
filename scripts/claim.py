#!/usr/bin/env python3
"""Claim an issue: label + comment + branch, in that order.

Comment before branch is load-bearing. Branch-first with a failed comment leaves
an active-looking branch and no claim record -- the state `workflow.md` calls
"looks active forever", which nothing later can tell from real work.
Comment-first fails the other way: a claim comment with no branch, which the
staleness rule reads as stale within `claim_expiry_hours` and reclaims cleanly.
Prefer the failure mode that expires on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402


def issue_state(issue, repo):
    raw = tracker.run("gh", "api", f"repos/{repo}/issues/{issue}",
                      "--jq", "{state: .state, title: .title}")
    return json.loads(raw)


def branch_exists(branch, cwd=None):
    return bool(tracker.run("git", "branch", "--list", branch, cwd=cwd).strip())


def resumes_own_claim(session, claim):
    """True when `claim`'s body already carries this run's `--session` value.

    Nothing else ever compares --session against a claim's body, so this is
    what lets a re-run recognise a claim comment ITS OWN prior attempt posted
    before failing on the branch step, and continue idempotently instead of
    reading its own live claim as someone else's and refusing.

    `session` truthy first: an empty string is a substring of every string,
    so without this guard an unattributed re-run (no --session at all) would
    look like a resume of a claim that is not its own.
    """
    return bool(session) and claim is not None and session in claim["body"]


def describe_claim(cfg, claim, issue, base, now, cwd=None):
    """The verdict, plus one line per half so the reader sees which one held.

    The two halves are rendered INDEPENDENTLY and the verdict comes from
    `tracker.is_stale`. Deriving a half from the verdict would print "EXPIRED"
    for a live claim on an inactive branch, which is the opposite of the rule.

    Takes `issue`, not a branch name: `tracker.resolve_branch()` finds the
    real branch by issue number across local AND remote-tracking refs, since
    the exact name `tracker.branch_name()` reconstructs is only ever a LOCAL
    ref's name -- silently reading "no branch" (and so "0 commits") for a
    branch that is only cut here and never pushed, which is what stealing
    live work from the two-half rule looks like. When nothing resolves, that
    is reported as such rather than folded into "0 commits", which reads as a
    measurement that was never actually made.
    """
    created = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
    # require(), not cfg[...]: claim_expiry_hours is NOT in REQUIRED_KEYS, so
    # load_config() returns happily without it and a direct index would exit on
    # an uncaught KeyError -- a traceback where TrackerError promises a message.
    hours = float(tracker.require(cfg, "claim_expiry_hours"))
    resolved = tracker.resolve_branch(cfg, issue, cwd=cwd)
    if resolved is None:
        seen = 0
        activity_line = f"  no branch found locally or on origin for #{issue}"
    else:
        seen = tracker.commits_since(resolved, base, created, cwd=cwd)
        activity_line = (
            f"  {resolved}: {seen} commits since"
            f"  -> {'INACTIVE' if seen == 0 else 'ACTIVE'}"
        )
    verdict = tracker.is_stale(created, seen, hours, now)
    age_h = (now - created).total_seconds() / 3600
    lines = [
        f"  claim comment: {int(age_h)}h ago (expires at {int(hours)}h)"
        f"  -> {'EXPIRED' if age_h >= hours else 'LIVE'}",
        activity_line,
    ]
    return verdict, lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claim")
    ap.add_argument("issue", type=int)
    ap.add_argument("--take", action="store_true",
                    help="reclaim a STALE claim, posting a takeover comment first")
    ap.add_argument("--session", default="",
                    help="session URL recorded in the claim comment")
    args = ap.parse_args(argv)

    cfg = tracker.config()
    repo = tracker.origin_repo()
    base = f"origin/{tracker.require(cfg, 'default_branch')}"
    now = datetime.now(timezone.utc)

    issue = issue_state(args.issue, repo)
    if issue["state"].lower() != "open":
        raise tracker.TrackerError(
            f"#{args.issue} is {issue['state'].lower()} -- nothing to claim."
        )

    branch = tracker.branch_name(cfg, args.issue, issue["title"])
    claim = tracker.latest_claim(args.issue, repo)

    # A prior run of THIS session can have posted the claim comment and then
    # failed before cutting the branch (a realistic failure: `origin/
    # <default_branch>` not fetched) -- without this, that re-run reads its
    # OWN live claim as someone else's and refuses, and --take is refused
    # too, since --take only fires on a stale verdict, locking the caller out
    # of their own issue for claim_expiry_hours. See resumes_own_claim().
    resuming = resumes_own_claim(args.session, claim)

    if claim is None:
        if branch_exists(branch):
            raise tracker.TrackerError(
                f"{branch} exists but #{args.issue} carries no claim comment. That "
                f"branch may hold work no comment ever recorded, so this is a "
                f"refusal, not an adoption -- there is deliberately no flag for it. "
                f"Inspect it, and delete it by hand if it is genuinely dead."
            )
    elif not resuming:
        verdict, lines = describe_claim(cfg, claim, args.issue, base, now)
        if verdict == "live" or not args.take:
            print(f"#{args.issue} is claimed.", file=sys.stderr)
            print("\n".join(lines), file=sys.stderr)
            raise tracker.TrackerError(
                "refusing: the claim is live." if verdict == "live"
                else "refusing. --take posts a takeover comment and reclaims."
            )

    label = tracker.status_label(cfg, "ongoing")
    if resuming:
        print(f"#{args.issue}'s live claim is already this session's -- "
              f"resuming at the branch step.")
    else:
        if label:
            try:
                tracker.run("gh", "issue", "edit", str(args.issue), "--repo", repo,
                            "--add-label", label)
            except tracker.TrackerError as exc:
                raise tracker.TrackerError(
                    f"{exc} -- nothing else has happened yet for #{args.issue}; "
                    f"safe to re-run."
                ) from exc
        else:
            print("status_labels.ongoing is not configured -- skipping the label half.")

        body = f"{tracker.CLAIM_MARKER}{args.session or '(session URL not supplied)'}"
        if claim is not None:
            body += (f"\n\nTakes over the claim posted at {claim['created_at']}, "
                     f"which expired. This comment is the live claim.")
        try:
            tracker.run("gh", "issue", "comment", str(args.issue), "--repo", repo,
                        "--body", body)
        except tracker.TrackerError as exc:
            after_label = (
                f" The label ({label}) was already applied to #{args.issue} and "
                f"nothing here removes it automatically -- left in place, it lies "
                f"about this issue's status until you drop it by hand."
                if label else
                f" No label is configured, so nothing else has happened yet for "
                f"#{args.issue}."
            )
            raise tracker.TrackerError(f"{exc}.{after_label}") from exc

    try:
        if not branch_exists(branch):
            tracker.run("git", "branch", branch, base)
    except tracker.TrackerError as exc:
        raise tracker.TrackerError(
            f"{exc} -- the claim comment for #{args.issue} was already posted"
            f"{f' and the label ({label}) applied' if label else ''}, and "
            f"nothing here rolls either back automatically. Re-run with the "
            f"same --session to resume at this step, or finish cutting "
            f"{branch} by hand."
        ) from exc
    print(f"claimed #{args.issue} -> {branch}")


if __name__ == "__main__":
    try:
        main()
    except tracker.TrackerError as exc:
        sys.exit(f"claim: {exc}")
