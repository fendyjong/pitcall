#!/usr/bin/env python3
"""File discovered work into the backlog milestone -- never the one in flight.

`--notes` files into `notes_milestone` instead: a RULED EXCEPTION, where the decision
was not to do the work, rather than deferred work someone still owes. Closing a backlog
issue means doing the work; closing a note means its reason stopped being true. Sharing
one milestone makes the two indistinguishable and the backlog's open count stops meaning
"work outstanding".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402


def build_command(cfg, repo, title, body_file, notes=False):
    """The `gh issue create` argv.

    Split out so the absence of a milestone override is provable without a
    network: the rule is "never to the milestone in flight", and a flag able to
    express the forbidden thing is a flag someone eventually passes.

    `notes` is a BOOLEAN for that same reason. It chooses between two milestones
    the config already fixed, so it cannot name the one in flight -- which an
    arbitrary `--milestone <name>` could. The key is required only when it is
    used, and missing it REFUSES rather than falling back to the backlog: a
    silent fallback is the defect this exists to remove, and it would look
    exactly like the old behaviour.
    """
    key = "notes_milestone" if notes else "backlog_milestone"
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title,
           "--milestone", tracker.require(cfg, key)]
    return cmd + (["--body-file", body_file] if body_file else ["--body", ""])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="file")
    ap.add_argument("title")
    ap.add_argument("--body-file", dest="body_file", default=None,
                    help="optional; a Backlog entry is a placeholder, written "
                         "up before it is claimed rather than before it is filed")
    ap.add_argument("--notes", action="store_true",
                    help="file into notes_milestone instead: a decision recorded "
                         "because it was ruled, not work someone still owes. Write "
                         "the body -- a note is only worth having if it says WHY")
    args = ap.parse_args(argv)

    cfg = tracker.config()
    print(tracker.run(*build_command(cfg, tracker.origin_repo(), args.title,
                                     args.body_file, notes=args.notes)).strip())


if __name__ == "__main__":
    try:
        main()
    except tracker.TrackerError as exc:
        sys.exit(f"file: {exc}")
