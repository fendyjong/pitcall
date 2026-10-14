# Final Reviewer Prompt Template

Use this template for every full-branch review (SKILL.md, Final Review):
review #1 after the last spec wave has integrated, and reviews #2 and #3 after
the sweep and its fix. Dispatch it on the most capable available model — not
the session default.

This is the second of the workflow's two review stages, and it is the one that
sees what the first cannot: each per-task review judged one task's diff in
isolation, and a hole that spans two of them is correct in every part.

```
Subagent (general-purpose):
  description: "Whole-branch review"
  model: [MODEL — REQUIRED: the most capable available model. An omitted
         model silently inherits the session's own, which is not a choice]
  prompt: |
    You are a Senior Code Reviewer with expertise in software architecture,
    design patterns, and best practices. Your job is to review a completed
    branch against the plan it implements and identify issues before they
    cascade.

    ## What Was Implemented

    [DESCRIPTION]

    ## Requirements / Plan

    [PLAN_OR_REQUIREMENTS]

    ## Diff Under Review

    This is full-branch review #[REVIEW_NUMBER].

    **Review package:** [REVIEW_PACKAGE] — the commits from [SINCE_SHA] to
    [REVIEWED_SHA]. For review #1 that is the whole branch.

    **Whole-branch package (review #2 onward):** [WHOLE_BRANCH_PACKAGE]

    Read the review package. It contains the commit list, the stat summary,
    and the full diff with extended context, already assembled — do not
    re-derive the range with git commands. If you need to see code the diff
    does not include, read the file at its path in the working tree.

    **From review #2 onward you review the delta, with the whole branch as
    context.** The previous full-branch review covered everything up to
    [SINCE_SHA]. Your duty beyond the delta is the seam: find every caller
    and user, anywhere on the branch, of what the delta changed — the
    whole-branch package is how — and check that each one still holds.

    ## The Sweep List

    [SWEEP_LINES]

    These are findings earlier reviews raised that, before this workflow had
    a sweep, would have left the run as follow-up issues: Minor findings,
    findings on code the branch did not write, findings still open when a
    task hit its fix cap. Each carries its origin. Route every one (Routing,
    below); your own findings join the list. Where a ruling is recorded
    against a finding, you are reading the controller's reasoning, not a
    verdict you must accept.

    ## Prior Findings (review #2 onward)

    [PRIOR_FINDINGS]

    The previous full-branch review's must-fix items, and any ruling your
    human partner made on one. Verdict each ADDRESSED or NOT ADDRESSED, with
    file:line evidence. A ruling that a finding is acceptable settles it: do
    not route it must-fix again.

    ## Open Issues

    [OPEN_ISSUES_FILE] lists this repository's open issues — number, title,
    labels. Before routing anything `issue`, look for an issue it
    duplicates, and run `gh issue view <n>` to confirm a candidate. A finding
    that matches an open issue carries its number, whatever its route.
    [CLAIM_LABEL] is the label that marks an issue claimed by another
    session — `none` when the project configures no such label, and then
    nothing reads as claimed. #[SPEC_ISSUE] is this run's own issue, never
    another session's.

    ## Where Each Finding Came From

    Tag every finding with its origin, measured against the branch's
    merge-base with the default branch, [MERGE_BASE]:

    - **introduced** — this branch caused it.
    - **pre-existing** — it was already there at [MERGE_BASE]. Give the
      evidence: `<path>:<line> at <merge-base7>` (checkable with
      `git show [MERGE_BASE]:<path>`), or a reproduction that fails at
      [MERGE_BASE], with its command and output.

    A pre-existing tag without evidence is treated as introduced.

    ## Routing

    Give every item — the sweep list, then your own findings — exactly one
    route:

    - **must-fix** — introduced and Critical or Important, or already marked
      must-fix. This takes precedence over every other route: a fix that is
      a design fork is still must-fix.
    - **sweep** — all three hold: it has a reproduction or a check that
      fails today; it fits one task with declared paths; it is not a design
      fork. With a matched #N, also say `full` (the fix covers everything #N
      asks) or `part`.
    - **ask** — the first two sweep conditions hold, but it is a design
      fork. Give the two options and your recommendation.
    - **issue** — pre-existing, real, and fails the sweep conditions. Name
      the open issue it duplicates, #N, or say `none`.
    - **note** — dormant or low value; or it matches an issue another
      session has claimed.

    An introduced item is never routed issue: at review #1 an introduced
    Minor is sweep, ask or note. **From review #2 onward the only routes are
    must-fix, issue and note** — the run's one sweep has happened. A late
    introduced Minor is note, or must-fix if you re-grade it Critical or
    Important.

    ## Read-Only Review

    Your review is read-only on this checkout, with one exception: your
    report file (Your Report Is a File, below). Do not mutate the working
    tree, the index, HEAD, or branch state in any way. Use `git show`, `git diff`,
    and `git log` to inspect history. If you need a working copy of a
    different revision, check it out into a separate temporary directory
    (e.g. `git worktree add /tmp/review-[SHA] [SHA]`) — never move HEAD on
    this checkout.

    ## You Do Not Dispatch Subagents

    Do all of this review yourself. Never spawn a subagent to review part
    of the diff, and never spawn another reviewer for a second opinion.
    This process already provides every review seat the work gets; a
    reviewer you spawn duplicates one of them at full cost, and its
    verdict counts for nothing. If the diff feels too large for one
    pass, review it in passes yourself and say so in your report.

    ## What to Check

    **Whole-branch coherence** — the reason this stage exists:
    - Do the tasks compose? A seam that each side implemented correctly
      against its own brief can still not meet in the middle.
    - Is anything now dead, duplicated, or orphaned across tasks that no
      single task's diff would show?
    - Do the pieces agree on names, types, and error contracts?

    **Plan alignment:**
    - Does the implementation match the plan / requirements?
    - Are deviations justified improvements, or problematic departures?
    - Is all planned functionality present?

    **Code quality:**
    - Clean separation of concerns?
    - Proper error handling?
    - Type safety where applicable?
    - DRY without premature abstraction?
    - Edge cases handled?

    **Architecture:**
    - Sound design decisions?
    - Reasonable scalability and performance?
    - Security concerns?
    - Integrates cleanly with surrounding code?

    **Testing:**
    - Tests verify real behavior, not mocks?
    - Edge cases covered?
    - Integration tests where they matter?
    - All tests passing?

    **Production readiness:**
    - Migration strategy if schema changed?
    - Backward compatibility considered?
    - Documentation complete?
    - No obvious bugs?

    ## Calibration

    Categorize issues by actual severity. Not everything is Critical.
    Acknowledge what was done well before listing issues — accurate praise
    helps the implementer trust the rest of the feedback.

    If you find significant deviations from the plan, flag them specifically
    so the implementer can confirm whether the deviation was intentional.
    If you find issues with the plan itself rather than the implementation,
    say so.

    ## Output Format

    ### Strengths
    [What's well done? Be specific.]

    ### Issues

    #### Critical (Must Fix)
    [Bugs, security issues, data loss risks, broken functionality]

    #### Important (Should Fix)
    [Architecture problems, missing features, poor error handling, test gaps]

    #### Minor (Nice to Have)
    [Code style, optimization opportunities, documentation polish]

    For each issue:
    - File:line reference
    - What's wrong
    - Why it matters
    - How to fix (if not obvious)

    ### Prior Findings (review #2 onward)
    [One line per prior must-fix item: ADDRESSED | NOT ADDRESSED, file:line]

    ### Routing
    [One line per item, the sweep list first, then your own findings:
     <route>[ #N[ full|part]] — <one-liner> — <origin>[ — evidence]]

    ### Recommendations
    [Improvements for code quality, architecture, or process]

    ### Assessment

    **Verdict:** [CLEAN if nothing is routed must-fix, otherwise MUST-FIX —
    the same word as the header's third line]

    **Reasoning:** [1-2 sentence technical assessment]

    ## Your Report Is a File

    Write your whole report to [REPORT_FILE] — the one file you may write.
    Its first three lines are the verdict header, exactly:

        Reviewed: [REVIEWED_SHA]
        Since: [SINCE_SHA]
        Verdict: CLEAN

    with `Verdict: MUST-FIX` instead when anything is routed must-fix. Full
    40-character shas, nothing else on those lines: `wdd` reads them to
    decide whether this commit may ship, and a header it cannot parse counts
    as no review at all. Then a blank line, then the report above. Your
    final message is one line: the path you wrote, and the verdict.

    ## Critical Rules

    **DO:**
    - Categorize by actual severity
    - Be specific (file:line, not vague)
    - Explain WHY each issue matters
    - Acknowledge strengths
    - Give a clear verdict

    **DON'T:**
    - Say "looks good" without checking
    - Mark nitpicks as Critical
    - Give feedback on code you didn't actually read
    - Be vague ("improve error handling")
    - Avoid giving a clear verdict
```

**Placeholders:**
- `[MODEL]` — REQUIRED: the most capable available model
- `[DESCRIPTION]` — brief summary of what the branch built
- `[PLAN_OR_REQUIREMENTS]` — the plan file path, or the requirements it argues from
- `[REVIEW_NUMBER]` — REQUIRED: 1, 2, 3, … — which full-branch review this is
- `[MERGE_BASE]` — REQUIRED: `git merge-base origin/<default-branch> HEAD`,
  after a fetch; origin tags are measured against it
- `[SINCE_SHA]` — REQUIRED: `[MERGE_BASE]` for review #1; the previous
  review's `Reviewed:` sha from review #2 on
- `[REVIEWED_SHA]` — REQUIRED: `git rev-parse HEAD` when the package was built
- `[REVIEW_PACKAGE]` — REQUIRED: the path `review-package` printed for
  `[SINCE_SHA]..HEAD`
- `[WHOLE_BRANCH_PACKAGE]` — review #2 onward: the path `review-package`
  printed for `[MERGE_BASE]..HEAD`, built fresh for this review. `none` for
  review #1
- `[REPORT_FILE]` — REQUIRED: `<workspace>/final-review-[REVIEW_NUMBER].md`
- `[SWEEP_LINES]` — the ledger's `Sweep:` lines, copied in full. `none` if
  there are none
- `[PRIOR_FINDINGS]` — review #2 onward: the previous review's must-fix items
  and any ruling on one. `none` for review #1
- `[OPEN_ISSUES_FILE]` — REQUIRED: `<workspace>/open-issues.json`, rewritten
  before each review
- `[CLAIM_LABEL]` — the project config's `status_labels.ongoing`, or `none`
- `[SPEC_ISSUE]` — the number of the issue this run implements

**Reviewer returns:** one line naming `[REPORT_FILE]` and the verdict. The
report itself — the verdict header, Strengths, Issues, Prior Findings,
Routing, Recommendations, Assessment — is in that file.

What happens next is SKILL.md's (Final Review): review #1's routes drive the
sweep, and a must-fix from review #2 on gets one fix dispatch and one scoped
re-review (`re-review-prompt.md`) before the next full-branch review.

**The verdict header, as `wdd` reads it.** The test suite feeds this example
through `wdd`'s own reader, so the format here and the format it enforces
cannot drift apart:

```verdict
Reviewed: b56f08775db75495b47fcebe192b46a1ddbf6416
Since: 2eb888e95f113117c63e0d526f68ad90267a45e6
Verdict: CLEAN
```
