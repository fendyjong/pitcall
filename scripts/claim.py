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
    """True when `claim`'s FIRST LINE names exactly this run's `--session`.

    Exact, never containment. Session identifiers share a fixed shape
    (`https://claude.ai/code/session_<id>`), so a truncated or prefix value is
    a substring of every OTHER session's claim -- and a run that mistook
    another session's live claim for its own would skip the refusal, the label
    and the comment, silently taking a claim it never posted.

    A minimum-length rule would be a self-assessment of how much uniqueness is
    enough, and it fails silently when it is wrong. Equality has no threshold
    to get wrong.

    Reads the FIRST line only: `--take` appends a takeover note below the
    marker line, so a later line can legitimately quote another session.
    """
    if not session or claim is None:
        return False
    first = claim["body"].split("\n", 1)[0].strip()
    if not first.startswith(tracker.CLAIM_MARKER):
        return False
    return first[len(tracker.CLAIM_MARKER):].strip() == session.strip()


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


def select_next(candidates, judge, take=False):
    """The first claimable issue and the ones passed over, or `(None, skipped)`.

    `candidates` is `(number, title)` in FIFO order; `judge(number)` returns
    `"free"`, `"live"` or `"stale"`. Pure given the judge, so the policy is
    provable without a network -- every judgement is an API call.

    **The walk is lazy on purpose.** It stops at the first claimable issue, so
    the common case costs one judgement rather than one per open issue.

    **A stale claim is passed over unless `take`.** `claim <n> --take` is a
    person naming an issue they looked at; letting a selector decide WHICH
    abandoned claim to take over is a different act, and not one this flag is
    asking for. A live claim is never taken, with or without `take`.

    `skipped` carries every rejection and its verdict, so a run that claims
    nothing can say what it considered instead of reporting a bare absence.
    """
    skipped = []
    for number, title in candidates:
        verdict = judge(number)
        if verdict == "free" or (verdict == "stale" and take):
            return (number, title), skipped
        skipped.append((number, verdict))
    return None, skipped


def resolve_next(cfg, repo, base, now, args):
    """The issue number `--next` picks, or a refusal naming what it passed over.

    The judge is where the network lives: one claim-comment lookup per
    candidate, run lazily by `select_next`, so the common case is a single
    call. `"branch"` is its own verdict rather than a free issue -- `claim <n>`
    refuses when a branch exists with no claim comment, and inheriting that
    refusal here would abort the whole walk on the first such issue instead of
    stepping past it.
    """
    candidates = tracker.open_issues(repo, args.milestone)
    titles = dict(candidates)

    def judge(number):
        claim = tracker.latest_claim(number, repo)
        if claim is not None:
            return describe_claim(cfg, claim, number, base, now)[0]
        if branch_exists(tracker.branch_name(cfg, number, titles[number])):
            return "branch"
        return "free"

    picked, skipped = select_next(candidates, judge, take=args.take)
    if picked is None:
        where = f" in milestone {args.milestone!r}" if args.milestone else ""
        if not skipped:
            raise tracker.TrackerError(f"no open issues{where} to claim.")
        detail = ", ".join(f"#{n} ({v})" for n, v in skipped)
        raise tracker.TrackerError(
            f"every open issue{where} is spoken for: {detail}."
            + ("" if args.take else
               " --take would reclaim a stale one, oldest first.")
        )
    number, title = picked
    if skipped:
        print(f"--next: passed over {', '.join(f'#{n} ({v})' for n, v in skipped)}")
    print(f"--next selected #{number}: {title}")
    return number


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claim")
    ap.add_argument("issue", type=int, nargs="?")
    ap.add_argument("--next", dest="pick_next", action="store_true",
                    help="claim the lowest-numbered open issue nothing holds")
    ap.add_argument("--milestone", default=None,
                    help="with --next, consider only this milestone")
    ap.add_argument("--take", action="store_true",
                    help="reclaim a STALE claim, posting a takeover comment first")
    ap.add_argument("--session", default="",
                    help="session URL recorded in the claim comment")
    args = ap.parse_args(argv)

    if (args.issue is None) == (not args.pick_next):
        raise tracker.TrackerError(
            "pass an issue number or --next, never both and never neither."
        )
    if args.milestone is not None and not args.pick_next:
        raise tracker.TrackerError(
            "--milestone narrows what --next considers; it means nothing "
            "beside an issue number you already chose."
        )

    cfg = tracker.config()
    repo = tracker.origin_repo()
    base = f"origin/{tracker.require(cfg, 'default_branch')}"
    now = datetime.now(timezone.utc)

    if args.pick_next:
        args.issue = resolve_next(cfg, repo, base, now, args)

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
