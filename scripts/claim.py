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


def describe_claim(cfg, claim, branch, base, now, cwd=None):
    """The verdict, plus one line per half so the reader sees which one held.

    The two halves are rendered INDEPENDENTLY and the verdict comes from
    `tracker.is_stale`. Deriving a half from the verdict would print "EXPIRED"
    for a live claim on an inactive branch, which is the opposite of the rule.
    """
    created = datetime.fromisoformat(claim["created_at"].replace("Z", "+00:00"))
    # require(), not cfg[...]: claim_expiry_hours is NOT in REQUIRED_KEYS, so
    # load_config() returns happily without it and a direct index would exit on
    # an uncaught KeyError -- a traceback where TrackerError promises a message.
    hours = float(tracker.require(cfg, "claim_expiry_hours"))
    seen = tracker.commits_since(branch, base, created, cwd=cwd) \
        if branch_exists(branch, cwd=cwd) else 0
    verdict = tracker.is_stale(created, seen, hours, now)
    age_h = (now - created).total_seconds() / 3600
    lines = [
        f"  claim comment: {int(age_h)}h ago (expires at {int(hours)}h)"
        f"  -> {'EXPIRED' if age_h >= hours else 'LIVE'}",
        f"  {branch}: {seen} commits since"
        f"  -> {'INACTIVE' if seen == 0 else 'ACTIVE'}",
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

    if claim is None:
        if branch_exists(branch):
            raise tracker.TrackerError(
                f"{branch} exists but #{args.issue} carries no claim comment. That "
                f"branch may hold work no comment ever recorded, so this is a "
                f"refusal, not an adoption -- there is deliberately no flag for it. "
                f"Inspect it, and delete it by hand if it is genuinely dead."
            )
    else:
        verdict, lines = describe_claim(cfg, claim, branch, base, now)
        if verdict == "live" or not args.take:
            print(f"#{args.issue} is claimed.")
            print("\n".join(lines))
            raise tracker.TrackerError(
                "refusing: the claim is live." if verdict == "live"
                else "refusing. --take posts a takeover comment and reclaims."
            )

    label = tracker.status_label(cfg, "ongoing")
    if label:
        tracker.run("gh", "issue", "edit", str(args.issue), "--repo", repo,
                    "--add-label", label)
    else:
        print("status_labels.ongoing is not configured -- skipping the label half.")

    body = f"{tracker.CLAIM_MARKER}{args.session or '(session URL not supplied)'}"
    if claim is not None:
        body += (f"\n\nTakes over the claim posted at {claim['created_at']}, "
                 f"which expired. This comment is the live claim.")
    tracker.run("gh", "issue", "comment", str(args.issue), "--repo", repo,
                "--body", body)

    if not branch_exists(branch):
        tracker.run("git", "branch", branch, base)
    print(f"claimed #{args.issue} -> {branch}")


if __name__ == "__main__":
    try:
        main()
    except tracker.TrackerError as exc:
        sys.exit(f"claim: {exc}")
