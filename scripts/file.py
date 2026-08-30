#!/usr/bin/env python3
"""File discovered work into the backlog milestone -- never the one in flight."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402


def build_command(cfg, repo, title, body_file):
    """The `gh issue create` argv.

    Split out so the absence of a milestone override is provable without a
    network: the rule is "never to the milestone in flight", and a flag able to
    express the forbidden thing is a flag someone eventually passes.
    """
    cmd = ["gh", "issue", "create", "--repo", repo, "--title", title,
           "--milestone", tracker.require(cfg, "backlog_milestone")]
    return cmd + (["--body-file", body_file] if body_file else ["--body", ""])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="file")
    ap.add_argument("title")
    ap.add_argument("--body-file", dest="body_file", default=None,
                    help="optional; a Backlog entry is a placeholder, written "
                         "up before it is claimed rather than before it is filed")
    args = ap.parse_args(argv)

    cfg = tracker.config()
    print(tracker.run(*build_command(cfg, tracker.origin_repo(),
                                     args.title, args.body_file)).strip())


if __name__ == "__main__":
    try:
        main()
    except tracker.TrackerError as exc:
        sys.exit(f"file: {exc}")
