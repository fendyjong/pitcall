#!/usr/bin/env python3
"""The solution-ready gate: is this issue its own spec, and is it still live?

Two gates doing two different jobs. The LABEL authorizes -- GitHub only lets a
user with triage or write permission apply one, so its presence means a
maintainer vouched, which matters because this repository is public and this
script executes a command out of an issue body. The `## Failing check` section
is the correctness gate, and it is read twice over: a check that FAILS means the
issue is live, and one that PASSES means it is already resolved.

That second reading is why this closes issues as a side effect of picking them
up -- a solution-ready backlog is staleness-tested by being used, rather than by
somebody auditing it.

The decision is a pure function with the fetching in a thin caller, the same
seam `tracker.py` documents: every case has to be provable offline, and a
decision tangled with its own fetching can only be exercised against a live
repository.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker  # noqa: E402

#: Applied by a maintainer. Authorizes; never asserts correctness.
LABEL = "solution-ready"

#: The body section carrying the check. The command is the first fenced block
#: under it; `Expected today:` records the failure as observed at filing.
HEADING = "## Failing check"

_EXPECTED = "Expected today:"

#: A fence is three-or-more backticks so a body can wrap an example that itself
#: contains a fence, which the documented format does.
_FENCE = re.compile(r"^[ \t]*(`{3,})[^\n]*$")

_BACKTICKED = re.compile(r"`([^`]+)`")

#: A check that never returns would hang the gate open. Bounded, and a timeout
#: is a refusal -- an unmeasured check is never a passed one.
TIMEOUT_SECONDS = 300


def extract_command(body):
    """The first fenced block under `HEADING`, or None.

    Scans forward from the heading rather than searching the whole body, so a
    fenced block appearing EARLIER (a "What" section quoting an error, say) is
    not mistaken for the check.
    """
    lines = body.split("\n")
    try:
        start = next(i for i, line in enumerate(lines)
                     if line.strip() == HEADING)
    except StopIteration:
        return None
    fence = None
    collected = []
    for line in lines[start + 1:]:
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            continue
        if match and len(match.group(1)) >= len(fence):
            break
        collected.append(line)
    if fence is None:
        return None
    command = "\n".join(collected).strip()
    return command or None


def expected_fragments(body):
    """Backticked fragments on the `Expected today:` line, in order.

    A line with no backticked fragment is treated as NO FAILURE RECORDED --
    which is what the close guard keys on. Comparing the whole free-text line
    would make "fails the way the body says" a judgement, in the one step whose
    entire value is being mechanical.
    """
    for line in body.split("\n"):
        if line.strip().startswith(_EXPECTED):
            return _BACKTICKED.findall(line)
    return []


def close_comment(command, output, when):
    """The evidence left behind when a passing check closes an issue."""
    return (
        f"Closing as completed: this issue's own `{HEADING}` now passes.\n\n"
        f"Checked on {when}:\n\n"
        f"```\n$ {command}\n{output}\n```\n\n"
        f"Filed with a failure observed at filing time, so the check was live "
        f"when the label was applied. It passes now, which is this format's "
        f"definition of resolved. Reopen if that reading is wrong."
    )


def decide(body, labels, result):
    """`(action, detail)` -- the whole gate, pure.

    `result` is None when the caller has not run the check yet; the answer is
    then `("run", command)` and the caller calls again with `(returncode,
    output)`. Holding both halves in one function keeps the ordering out of the
    caller, which is where it would be got wrong.

    Actions: `run`, `proceed`, `close`, `refuse`.
    """
    if LABEL not in labels:
        return ("refuse", f"not labelled {LABEL} -- this takes the ordinary path.")
    command = extract_command(body)
    if command is None:
        return ("refuse",
                f"labelled {LABEL} but carries no `{HEADING}` section with a "
                f"fenced command. The label authorizes; the section is what "
                f"makes the classification checkable.")
    if result is None:
        return ("run", command)

    returncode, output = result
    fragments = expected_fragments(body)
    if not fragments:
        return ("refuse",
                f"`{_EXPECTED}` records no backticked fragment, so the check "
                f"was never seen to fail and the label was never earned. "
                f"Refusing rather than acting on it.")
    if returncode == 0:
        return ("close",
                "the check passes, and the body records an observed failure at "
                "filing time -- the issue is resolved.")
    if any(fragment in output for fragment in fragments):
        return ("proceed",
                "the check fails as recorded -- the classification is verified "
                "by evidence.")
    return ("refuse",
            f"the check fails, but not the failure `{_EXPECTED}` records "
            f"({fragments[0]!r} does not appear in the output). Something else "
            f"is broken, or the body is stale.")


def issue_state(issue, repo):
    raw = tracker.run("gh", "api", f"repos/{repo}/issues/{issue}",
                      "--jq", "{body: .body, labels: [.labels[].name]}")
    return json.loads(raw)


def run_check(command, cwd=None):
    """`(returncode, combined output)` for `command`, or a TrackerError.

    Split with `shlex` and run WITHOUT a shell. The body is attacker-adjacent
    text in a public repository -- the label is what keeps a stranger's issue
    from reaching here at all, and narrowing the executed surface to one program
    plus arguments is the second half of that. A check genuinely needing a pipe
    is a check that needs a script, which the body can name instead.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise tracker.TrackerError(
            f"cannot parse the check as a command line: {exc}"
        ) from exc
    if not argv:
        raise tracker.TrackerError("the check is empty.")
    try:
        completed = subprocess.run(argv, capture_output=True, text=True,
                                   cwd=cwd, timeout=TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise tracker.TrackerError(f"the check names no runnable program: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise tracker.TrackerError(
            f"the check did not finish within {TIMEOUT_SECONDS}s. An unmeasured "
            f"check is never a passed one, so this refuses."
        ) from exc
    return (completed.returncode, (completed.stdout or "") + (completed.stderr or ""))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="solution-ready")
    ap.add_argument("issue", type=int)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the check and print the verdict, but never close "
                         "the issue or comment on it")
    args = ap.parse_args(argv)

    repo = tracker.origin_repo()
    state = issue_state(args.issue, repo)
    body, labels = state["body"] or "", state["labels"]

    action, detail = decide(body, labels, None)
    if action == "refuse":
        raise tracker.TrackerError(f"#{args.issue}: {detail}")

    command = detail
    print(f"#{args.issue}: running the recorded check\n  $ {command}")
    result = run_check(command)
    action, detail = decide(body, labels, result)

    if action == "refuse":
        raise tracker.TrackerError(f"#{args.issue}: {detail}")

    if action == "proceed":
        print(f"#{args.issue}: {detail}")
        print("solution-ready: proceed")
        return 0

    comment = close_comment(command, result[1].strip(), date.today().isoformat())
    if args.dry_run:
        print(f"#{args.issue}: {detail}")
        print("--dry-run, so nothing was written. The comment would be:\n")
        print(comment)
        return 0
    tracker.run("gh", "issue", "comment", str(args.issue), "--repo", repo,
                "--body", comment)
    tracker.run("gh", "issue", "close", str(args.issue), "--repo", repo,
                "--reason", "completed")
    print(f"#{args.issue}: {detail}")
    print(f"closed #{args.issue} as completed, with the check's output as evidence.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except tracker.TrackerError as exc:
        sys.exit(f"solution-ready: {exc}")
