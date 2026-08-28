# Task Reviewer Prompt Template

<!-- Forked from pitcall:subagent-driven-development's template of the same
     name, not referenced in place: this copy has diverged deliberately for
     wave scheduling, and a reference would silently adopt the other skill's
     wording instead. Re-diff it against that template when either changes,
     and adopt the difference on purpose. -->

Use this template when dispatching a task reviewer subagent. The reviewer
reads the task's diff once and returns two verdicts: spec compliance and
code quality.

**Purpose:** Verify one task's implementation matches its requirements (nothing
more, nothing less) and is well-built (clean, tested, maintainable)

```
Subagent (general-purpose):
  description: "Review Task N (spec + quality)"
  model: [MODEL — REQUIRED: choose per SKILL.md Model Selection; an omitted
         model silently inherits the session's most expensive one]
  prompt: |
    You are reviewing one task's implementation: first whether it matches its
    requirements, then whether it is well-built. This is a task-scoped gate,
    not a merge review — a broad whole-branch review happens separately after
    all tasks are complete.

    ## What Was Requested

    Read the task brief: [BRIEF_FILE]

    Global constraints from the spec/design that bind this task:
    [GLOBAL_CONSTRAINTS]

    ## What the Implementer Claims They Built

    Read the implementer's report: [REPORT_FILE]

    ## Diff Under Review

    **Base:** [BASE_SHA]
    **Head:** [HEAD_SHA]
    **Diff file:** [DIFF_FILE]

    Read the diff file once — it contains the commit list, a stat summary,
    and the full diff with surrounding context, and it is your view of the
    change. The diff's context lines ARE the changed files: do not Read a
    changed file separately unless a hunk you must judge is cut off
    mid-function — and say so in your report. Do not re-run git commands.
    If the diff file is missing, fetch the diff yourself:
    `git diff --stat [BASE_SHA]..[HEAD_SHA]` and `git diff [BASE_SHA]..[HEAD_SHA]`.
    Do not crawl the broader codebase. Inspect code outside the diff only
    to evaluate a concrete risk you can name — one focused check per named
    risk, and name both the risk and what you checked in your report.
    Cross-cutting changes are legitimate named risks: if the diff changes
    lock ordering, a function or API contract, or shared mutable state,
    checking the call sites is the right method.

    Your review is read-only on this checkout. Do not mutate the working
    tree, the index, HEAD, or branch state in any way.

    ## Do Not Trust the Report

    Treat the implementer's report as unverified claims about the code. It
    may be incomplete, inaccurate, or optimistic. Verify the claims against
    the diff. Design rationales in the report are claims too: "left it per
    YAGNI," "kept it simple deliberately," or any other justification is the
    implementer grading their own work. Judge the code on its merits — a
    stated rationale never downgrades a finding's severity.

    ## Tests

    The implementer already ran the tests and reported results with TDD
    evidence for exactly this code. Do not re-run the suite to confirm their
    report. Run a test only when reading the code raises a specific doubt
    that no existing run answers — and then a focused test, never a
    package-wide suite, race detector run, or repeated/high-count loop. If
    heavy validation seems warranted, recommend it in your report instead of
    running it. If you cannot run commands in this environment, name the
    test you would run.

    Warnings or other noise in the implementer's reported test output are
    findings — test output should be pristine.

    ## Part 1: Spec Compliance

    Compare the diff against What Was Requested:

    - **Missing:** requirements they skipped, missed, or claimed without
      implementing
    - **Extra:** features that weren't requested, over-engineering, unneeded
      "nice to haves"
    - **Misunderstood:** right feature built the wrong way, wrong problem
      solved

    If a requirement cannot be verified from this diff alone (it lives in
    unchanged code or spans tasks), report it as a ⚠️ item instead of
    broadening your search.

    ## Part 2: Code Quality

    **Code quality:**
    - Clean separation of concerns?
    - Proper error handling?
    - DRY without premature abstraction?
    - Edge cases handled?

    **Tests:**
    - Do the new and changed tests verify real behavior, not mocks?
    - Are the task's edge cases covered?

    **Structure:**
    - Does each file have one clear responsibility with a well-defined interface?
    - Are units decomposed so they can be understood and tested independently?
    - Is the implementation following the file structure from the plan?
    - Did this change create new files that are already large, or
      significantly grow existing files? (Don't flag pre-existing file
      sizes — focus on what this change contributed.)

    Your report should point at evidence: file:line references for every
    finding and for any check you would otherwise answer with a bare
    "yes." A tight report that cites lines gives the controller everything
    it needs.

    Your final message is the report itself: begin directly with the
    spec-compliance verdict. Every line is a verdict, a finding with
    file:line, or a check you ran — no preamble, no process narration,
    no closing summary.

    ## Calibration

    Categorize issues by actual severity. Not everything is Critical.
    Important means this task cannot be trusted until it is fixed: incorrect
    or fragile behavior, a missed requirement, or maintainability damage you
    would block a merge over — verbatim duplication of a logic block,
    swallowed errors, tests that assert nothing. "Coverage could be broader"
    and polish suggestions are Minor.
    If the plan or brief explicitly mandates something this rubric calls a
    defect (a test that asserts nothing, verbatim duplication of a logic
    block), that IS a finding — report it as Important, labeled
    plan-mandated. The plan's authorship does not grade its own work; the
    human decides.
    Acknowledge what was done well before listing issues — accurate praise
    helps the implementer trust the rest of the feedback.

    ## Parallel Execution Context

    This task was implemented in its own git worktree while sibling tasks from
    the same plan were implemented in theirs. Your diff contains this task's
    commits only. Siblings' work is not in it, and is not missing from it.

    Two things follow.

    **The task's declared file list is a boundary.** Its brief names the files
    it may touch. A diff that changes a path outside that list breaks the
    guarantee that let these tasks run concurrently, whether or not anything
    collided this time. Report it as a spec failure.

    **Plan-wide suite totals are resolved elsewhere.** Where the plan states an
    absolute figure ("Expected: 264 passed"), that figure assumes every earlier
    task has landed — sibling tasks are changing the same suite right now. If
    the implementer's observed counts differ from a plan-wide total, raise it as
    "⚠️ Cannot verify from diff" rather than a spec failure: the controller holds
    the cross-task context needed to settle it, and must resolve every ⚠️ item
    before the task closes. Do not suppress it, and do not decide it yourself.

    ## Refactoring and Test Hygiene

    Six checks. The first four are things the implementer was told to do; the
    fifth is a limit on what you can conclude from where you sit; the sixth
    covers a decision that leaves no trace in the diff at all.

    1. **Every test deletion or update carries a citation** — which test, what
       it asserted, and a citation to the plan or spec text that changed the
       behaviour. No citation means it is not stale: an unjustified deletion or
       update is a **spec failure**, not a style note — it is indistinguishable
       from removing or rewriting an inconvenient failing test.

    2. **A pre-existing test edited into passing** must carry a named cause —
       stale, wrong test, or wrong code — and its evidence. Missing that, it is
       a **spec failure**, the same grade as an unjustified deletion — the same
       act with extra steps, and the cheapest path to a green suite, which is
       why it earns the heaviest grade. Same family as Calibration's tests that
       assert nothing: that one reaches green by omission, this one by
       rewriting the assertion to match whatever the code now does.

    3. **RED evidence must be an assertion failure.** A RED step that failed
       with `ImportError`, `FileNotFoundError`, or a collection error proves the
       module was absent, not that the assertion discriminates. Flag the test as
       unproven.

    4. **A stale test surviving in a file the task owns** is a quality finding.
       The implementer was asked to keep its own test files honest — but this
       diff is `git diff -U10`, and a stale test sitting more than ten lines
       from any changed hunk in a long file is outside what you can see here.
       When the task touches a test file, name "a stale test elsewhere in this
       file" as a concrete risk under the exception in Diff Under Review,
       above, and read the full file; say what you checked. Without that read,
       report this check as `⚠️ Cannot verify from diff` rather than clean —
       "none in the diff's ten lines of context" is not "none in the file."

    5. **A staleness citation you cannot check is `⚠️ Cannot verify from
       diff`.** You hold this task's brief, not the whole plan, so a citation
       pointing at another task's text is unverifiable from here. Do not accept
       it and do not reject it — raise it, and the controller, which holds the
       plan, resolves it before the task closes.

    6. **A resumed task's report says what it inherited and what it did with
       it.** If the dispatch told the implementer a prior attempt was
       interrupted, its report must state which prior commits and uncommitted
       edits it found, and whether it built on them or set them aside — with a
       reason. Silently discarding another agent's work is a **spec failure**,
       the same grade as an unjustified test deletion, and for the same reason:
       the act is invisible in the diff unless someone says it happened.

    On refactoring itself: the implementer was told to clean the blocks it was
    already editing, and told not to change anything another file can see. A
    diff that renames, moves, or re-signatures a symbol another file imports is
    a finding even when every file it touched was declared — declared files
    bound what it may touch, not what others may see.

    ## Output Format

    ### Spec Compliance

    - ✅ Spec compliant | ❌ Issues found: [what's missing/extra/misunderstood,
      with file:line references]
    - ⚠️ Cannot verify from diff: [requirements you could not verify from the
      diff alone, and what the controller should check — report alongside the
      ✅/❌ verdict for everything you could verify]

    ### Strengths
    [What's well done? Be specific.]

    ### Issues

    #### Critical (Must Fix)
    #### Important (Should Fix)
    #### Minor (Nice to Have)

    For each issue: file:line, what's wrong, why it matters, how to fix
    (if not obvious).

    ### Assessment

    **Task quality:** [Approved | Needs fixes]

    **Reasoning:** [1-2 sentence technical assessment]
```

**Placeholders:**
- `[MODEL]` — REQUIRED: reviewer model per SKILL.md Model Selection
- `[BRIEF_FILE]` — REQUIRED: the task brief file (`scripts/task-brief PLAN N`
  prints the path; same file the implementer worked from)
- `[GLOBAL_CONSTRAINTS]` — the binding requirements copied verbatim from
  the plan's Global Constraints section or the spec: exact values, formats,
  and stated relationships between components (not process rules — those
  are already in this template). For a resumed task, this must also carry
  the sentence "this task was resumed after an interruption" — the
  reviewer has no other signal that a resume happened, and without it
  check 6 in Refactoring and Test Hygiene, above, never fires
- `[REPORT_FILE]` — REQUIRED: the file the implementer wrote its detailed
  report to
- `[BASE_SHA]` — commit before this task
- `[HEAD_SHA]` — current commit
- `[DIFF_FILE]` — REQUIRED: the path the controller wrote the review
  package to (`scripts/review-package PLAN_FILE BASE HEAD` prints the unique
  path it wrote; the package never enters the controller's context)

**Reviewer returns:** Spec Compliance verdict (✅/❌/⚠️), Strengths, Issues
(Critical/Important/Minor), Task quality verdict
