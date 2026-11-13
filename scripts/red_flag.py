#!/usr/bin/env python3
"""Mark an issue blocked, recording on what -- or clear the mark.

Blocking is not unclaiming. `status_labels.blocked` sits beside `ongoing` or
`in_review` rather than replacing it: a blocked issue's branch and work are
real, and dropping the claim is how a second session ends up duplicating them.
So nothing here reads, adds or removes any label but `blocked`.

The label is the state; the `blocked on: ` comment only holds the reason. The
label goes on first, for the reason `enter` puts its label on first: a label
`gh` refuses -- one the project never created -- then aborts before anything
else has happened.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402


def blocked_label(cfg):
    """`status_labels.blocked`, or a refusal: with no label there is no block."""
    label = tracker.status_label(cfg, "blocked")
    if not label:
        raise tracker.TrackerError(
            "status_labels.blocked is not configured -- nothing to mark the "
            "issue with, so nothing counts as blocked."
        )
    return label


def mark_commands(label, repo, issue, state, reason):
    """The argv for each marking step, in order: the label, then the reason.

    Pure, like `file.py`'s `build_command`, so the refusals and the order are
    provable without a network.
    """
    reason = reason.strip()
    if not reason:
        raise tracker.TrackerError(
            "the reason is blank -- say what the issue is blocked on.")
    if state.lower() != "open":
        raise tracker.TrackerError(
            f"#{issue} is {state.lower()} -- only an open issue can be blocked.")
    return [
        ["gh", "issue", "edit", str(issue), "--repo", repo, "--add-label", label],
        ["gh", "issue", "comment", str(issue), "--repo", repo,
         "--body", tracker.BLOCK_MARKER + reason],
    ]


def clear_command(label, repo, issue, labels):
    """The argv that removes the blocked label, or None when it is not on.

    Takes no state: clearing works on a closed issue, because the final review
    clears a block after the merge that closed it. Posts no comment -- the
    timeline already records who removed the label, and when.
    """
    if label not in labels:
        return None
    return ["gh", "issue", "edit", str(issue), "--repo", repo,
            "--remove-label", label]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="red-flag")
    ap.add_argument("issue", type=int)
    ap.add_argument("reason", nargs="?", default=None,
                    help="what the issue is blocked on")
    ap.add_argument("--clear", action="store_true", help="clear the block")
    args = ap.parse_args(argv)

    if (args.reason is None) == (not args.clear):
        raise tracker.TrackerError(
            "pass a reason or --clear, never both and never neither.")

    label = blocked_label(tracker.config())
    repo = tracker.origin_repo()
    issue = tracker.issue_state(args.issue, repo)

    if args.clear:
        cmd = clear_command(label, repo, args.issue, issue["labels"])
        if cmd is None:
            print(f"#{args.issue} is not blocked")
            return
        tracker.run(*cmd)
        print(f"#{args.issue} is no longer blocked")
        return

    edit, comment = mark_commands(label, repo, args.issue, issue["state"],
                                  args.reason)
    try:
        tracker.run(*edit)
    except tracker.TrackerError as exc:
        raise tracker.TrackerError(
            f"{exc} -- nothing else has happened yet for #{args.issue}; "
            f"safe to re-run."
        ) from exc
    try:
        tracker.run(*comment)
    except tracker.TrackerError as exc:
        raise tracker.TrackerError(
            f"{exc}. {label} is on #{args.issue}, but the reason was not "
            f"posted -- claim shows the previous reason, if any. Re-run to "
            f"post it: the label step is a no-op when the label is already on."
        ) from exc
    print(f"#{args.issue} is blocked on: {args.reason.strip()}")


if __name__ == "__main__":
    try:
        main()
    except tracker.TrackerError as exc:
        sys.exit(f"red-flag: {exc}")
