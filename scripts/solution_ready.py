#!/usr/bin/env python3
"""The solution-ready gate: is this issue its own spec, and is it still live?

Two gates doing two different jobs. AUTHORIZATION is the label plus the issue
author's association with the repository -- GitHub only lets a user with triage
or write permission apply a label, so its presence means a maintainer vouched;
but a label authorizes a SNAPSHOT while the body is fetched live at run time,
and the author can rewrite it afterwards. So the author has to be on the
maintainer side of the boundary too. Both matter because this repository is
public and this script executes a command out of an issue body. The
`## Failing check` section is the correctness gate, and it is read twice over:
a check that FAILS means the issue is live, and one that PASSES means it is
already resolved.

That second reading is why this closes issues as a side effect of picking them
up -- a solution-ready backlog is staleness-tested by being used, rather than by
somebody auditing it.

The exit status is three-way, because the caller of this script routes on it and
`close` is a do-NOT-proceed outcome:

    0  proceed -- the check fails as recorded; hand the issue to the next stage
    3  close   -- the check passes; the issue was already resolved and is now
                 closed. The next stage must NOT run. Prints `solution-ready:
                 closed`. `--dry-run` reports the same verdict and the same 3;
                 the status reports the verdict, not whether anything was written.
    1  refuse  -- anything else, with the reason on stderr

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

#: `author_association` values that put the issue's author on the maintainer
#: side of the trust boundary. The label authorizes the body as it stood when
#: the label was applied; the body is read live, and its author is the one
#: person who can change it afterwards. So the author is gated too, and an
#: outsider's body is never executed however it came to be labelled.
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")

#: The body section carrying the check. The command is the first fenced block
#: under it; `Expected today:` records the failure as observed at filing.
HEADING = "## Failing check"

_EXPECTED = "Expected today:"

#: A fence is three-or-more backticks so a body can wrap an example that itself
#: contains a fence, which the documented format does.
_FENCE = re.compile(r"^[ \t]*(`{3,})[^\n]*$")

_BACKTICKED = re.compile(r"`([^`]+)`")

#: Bare tokens that mean something to a shell and nothing to `execve`. The
#: check runs WITHOUT a shell, so these would be handed to the program as
#: literal arguments and the verdict derived from whatever it made of them.
#: Refusing loudly is the half that protects the filer; only a token that is
#: ENTIRELY an operator counts, because `a|b` is a legitimate regex argument.
_SHELL_TOKENS = frozenset(
    {"|", "||", "&", "&&", ";", ";;", ">", ">>", "<", "<<", "2>", "2>&1", "&>"}
)

#: A check that never returns would hang the gate open. Bounded, and a timeout
#: is a refusal -- an unmeasured check is never a passed one.
TIMEOUT_SECONDS = 300

#: A check's output goes into a closing comment, and GitHub caps a comment at
#: roughly 65,536 characters. The tail is the part that carries the verdict.
MAX_OUTPUT_CHARS = 4000


def _section_lines(body):
    """The lines following `HEADING`, or None when the body has no such section.

    Both readers of the section scan forward from here rather than searching the
    whole body. A body that quotes the documented example ABOVE its own section
    -- exactly what copying the README produces -- otherwise hands each reader a
    different section's answer, and the close guard is the reader where that is
    a fail-OPEN: the example's `Expected today:` fragment would satisfy a guard
    about a check the example never ran.

    A heading INSIDE a fenced block is quoted text, not a section, for the same
    reason: the documented example is a fenced block that contains this exact
    heading, so pasting it would otherwise make the example the section.
    """
    lines = body.split("\n")
    fence = None
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif len(match.group(1)) >= len(fence):
                fence = None
            continue
        if fence is None and line.strip() == HEADING:
            return lines[i + 1:]
    return None


def extract_command(body):
    """The first fenced block under `HEADING`, or None.

    Scans forward from the heading rather than searching the whole body, so a
    fenced block appearing EARLIER (a "What" section quoting an error, say) is
    not mistaken for the check. A block whose closing fence never arrives is
    not a command either: taking everything to end-of-body would execute the
    rest of the issue.
    """
    lines = _section_lines(body)
    if lines is None:
        return None
    fence = None
    closed = False
    collected = []
    for line in lines:
        match = _FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            continue
        if match and len(match.group(1)) >= len(fence):
            closed = True
            break
        collected.append(line)
    if fence is None or not closed:
        return None
    command = "\n".join(collected).strip()
    return command or None


def expected_fragments(body):
    """Backticked fragments on the section's `Expected today:` line, in order.

    Bounded to the same forward scan `extract_command` makes, so the fragment
    and the command always come from one section -- see `_section_lines`.

    A line with no backticked fragment is treated as NO FAILURE RECORDED --
    which is what the close guard keys on. Comparing the whole free-text line
    would make "fails the way the body says" a judgement, in the one step whose
    entire value is being mechanical.
    """
    lines = _section_lines(body)
    if lines is None:
        return []
    for line in lines:
        if line.strip().startswith(_EXPECTED):
            return _BACKTICKED.findall(line)
    return []


def _fence_for(*texts):
    """A fence longer than the longest backtick run in `texts`.

    Command output is arbitrary text: a three-backtick fence around output that
    itself contains one closes early and the rest renders as prose.
    """
    longest = 0
    for text in texts:
        for run in re.findall(r"`+", text or ""):
            longest = max(longest, len(run))
    return "`" * max(4, longest + 1)


def _truncate(output):
    """`(text, was_truncated)` -- the last `MAX_OUTPUT_CHARS` of `output`."""
    if len(output) <= MAX_OUTPUT_CHARS:
        return output, False
    return output[-MAX_OUTPUT_CHARS:], True


def close_comment(command, output, when):
    """The evidence left behind when a passing check closes an issue."""
    shown, truncated = _truncate(output)
    fence = _fence_for(command, shown)
    note = (
        f"\n\nOutput truncated to the last {MAX_OUTPUT_CHARS} characters."
        if truncated else ""
    )
    return (
        f"Closing as completed: this issue's own `{HEADING}` now passes.\n\n"
        f"Checked on {when}:\n\n"
        f"{fence}\n$ {command}\n{shown}\n{fence}{note}\n\n"
        f"Filed with a failure observed at filing time, so the check was live "
        f"when the label was applied. It passes now, which is this format's "
        f"definition of resolved. Reopen if that reading is wrong."
    )


def decide(body, labels, association, result):
    """`(action, detail)` -- the whole gate, pure.

    `association` is the issue author's `author_association`; it and `labels`
    are the authorization half, `body` and `result` the correctness half.

    `result` is None when the caller has not run the check yet; the answer is
    then `("run", command)` and the caller calls again with `(returncode,
    output)`. Holding both halves in one function keeps the ordering out of the
    caller, which is where it would be got wrong.

    Actions: `run`, `proceed`, `close`, `refuse`.
    """
    if LABEL not in labels:
        return ("refuse", f"not labelled {LABEL} -- this takes the ordinary path.")
    if association not in TRUSTED_ASSOCIATIONS:
        return ("refuse",
                f"labelled {LABEL}, but the issue's author is {association!r} "
                f"rather than one of {', '.join(TRUSTED_ASSOCIATIONS)}. The "
                f"label authorizes the body as it stood when it was applied, "
                f"and the body is read live -- its author can rewrite the "
                f"command after labelling. An outsider's body is never run.")
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
                      "--jq", "{body: .body, labels: [.labels[].name], "
                              "association: .author_association}")
    return json.loads(raw)


def run_check(command, cwd=None):
    """`(returncode, combined output)` for `command`, or a TrackerError.

    Split with `shlex` and run WITHOUT a shell. The body is attacker-adjacent
    text in a public repository -- the label and the author gate are what keep a
    stranger's issue from reaching here at all, and narrowing the executed
    surface to one program plus arguments is the second half of that. A check
    genuinely needing a pipe is a check that needs a script, which the body can
    name instead.

    Because there is no shell, a `|` or `2>&1` in the check would be passed to
    the program as a literal argument and the verdict derived from whatever it
    made of them. That is a silent wrong answer, so it refuses instead.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise tracker.TrackerError(
            f"cannot parse the check as a command line: {exc}"
        ) from exc
    if not argv:
        raise tracker.TrackerError("the check is empty.")
    operators = [token for token in argv if token in _SHELL_TOKENS]
    if operators:
        raise tracker.TrackerError(
            f"the check uses the shell operator {operators[0]!r}, but checks run "
            f"WITHOUT a shell -- one program plus its arguments, no pipes, "
            f"redirects, `&&` or `cd`. Passing it through would hand "
            f"{operators[0]!r} to {argv[0]!r} as a literal argument and read the "
            f"verdict off the result. A check that needs a shell needs a script, "
            f"which the body can name instead."
        )
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
    association = state.get("association")

    action, detail = decide(body, labels, association, None)
    if action == "refuse":
        raise tracker.TrackerError(f"#{args.issue}: {detail}")

    command = detail
    print(f"#{args.issue}: running the recorded check\n  $ {command}")
    result = run_check(command)
    action, detail = decide(body, labels, association, result)

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
        print("solution-ready: closed")
        return 3
    # Comment BEFORE close: a failed comment must leave the issue open rather
    # than closed with no evidence of why.
    tracker.run("gh", "issue", "comment", str(args.issue), "--repo", repo,
                "--body", comment)
    tracker.run("gh", "issue", "close", str(args.issue), "--repo", repo,
                "--reason", "completed")
    print(f"#{args.issue}: {detail}")
    print(f"closed #{args.issue} as completed, with the check's output as evidence.")
    print("solution-ready: closed")
    return 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except tracker.TrackerError as exc:
        sys.exit(f"solution-ready: {exc}")
