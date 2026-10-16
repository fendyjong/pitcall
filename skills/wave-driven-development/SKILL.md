---
name: wave-driven-development
description: Use when taking an approved spec through implementation to a mergeable branch, in the current session
---

# Wave-Driven Development

Take an approved spec to a green branch: plan the work into waves that cannot
conflict, run each wave's tasks concurrently in isolated worktrees, merge at
every wave boundary, and finish with full-branch review, one sweep of what the
run found, and one PR.

**Core principle:** conflicts are prevented at planning time, not resolved at
merge time. Within a wave, tasks have disjoint file sets and none depends on a
sibling — so merges are clean by construction and merge order does not matter.

**Three phases.** Phase 1 plans and validates. Phase 2 executes. Phase 3
ships and cleans up. Do not begin Phase 2 with a plan that has not passed
validation, and do not begin Phase 3 with a wave that has not integrated.

**Why subagents.** You delegate each task to an agent with an isolated
context. By crafting its instructions precisely you keep it focused, and it
never inherits this session's history — you construct exactly what it needs.
That also preserves your own context for coordination, which is the only work
you do.

**Narration:** between tool calls, narrate at most one short line — the ledger
and the tool results carry the record.

## When to Use

- **Have an approved spec, nothing planned yet** → start at Phase 1 — Plan.
- **Already have a wave plan that has passed `--validate`** → skip straight
  to Phase 2 — Execute.

There is no separate mode for a serial plan. A task worktree exists to isolate
concurrent writers, so a wave of width 1 has nothing to isolate from and does
not get one — the same rule, applied to a wave that happens to hold one task
(Phase 2, per-wave step 1). Every other gate is unchanged.

## Phase 1 — Plan

Input: an approved spec. Output: a wave plan that has passed `--validate`.

Write the plan for an engineer with zero context for this codebase and
questionable taste: which files to touch for each task, the code, the tests,
the docs they might need, how to verify it. Assume a skilled developer who
knows almost nothing about this toolset or problem domain, and who does not
know good test design well. DRY, YAGNI, TDD, frequent commits.

Save the plan to the project's `plan_dir` (`scripts/project-config --scalar plan_dir`),
named `YYYY-MM-DD-<feature-name>.md` (a stated user preference for plan location
overrides this).

### Scope check

If the spec covers several independent subsystems, it should have been broken
into sub-project specs during brainstorming. If it was not, say so and suggest
one plan per subsystem. Each plan must produce working, testable software on
its own.

### File structure

Before defining tasks, map out which files will be created or modified and
what each is responsible for. This is where decomposition decisions get locked
in — and under this skill it also decides which work can ever share a wave, so
it is the first thing to get right, not a formality.

- Design units with clear boundaries and well-defined interfaces. Each file
  has one clear responsibility.
- You reason best about code you can hold in context at once, and edits are
  more reliable when files are focused. Prefer smaller, focused files.
- Files that change together live together. Split by responsibility, not by
  technical layer.
- In existing codebases, follow established patterns. If the codebase uses
  large files, do not unilaterally restructure — but if a file being modified
  has grown unwieldy, planning a split is reasonable.

### Task right-sizing

A task is the smallest unit that carries its own test cycle and is worth a
fresh reviewer's gate. Fold setup, configuration, scaffolding, and
documentation steps into the task whose deliverable needs them; split only
where a reviewer could meaningfully reject one task while approving its
neighbour. Each task ends with an independently testable deliverable.

**Small same-shape work is one task, not many.** Where the same one-line fix,
constant change, or field addition repeats across several files, draw it as a
single task listing every file and its change — one worktree, one review
surface. Reserve a task of its own for work that needs its own judgment, its
own tests, or its own review. This is a planning decision here rather than a
dispatch-time one: a task is the unit that gets a worktree and a branch, so
merging small work into one task is the only way to avoid paying that overhead
several times over.

**Each step inside a task is one action, 2–5 minutes:**

- "Write the failing test" — step
- "Run it to make sure it fails" — step
- "Implement the minimal code to make the test pass" — step
- "Run the tests and make sure they pass" — step
- "Commit" — step

### The plan document

**Every plan starts with this header:**

```markdown
# [Feature Name] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use pitcall:wave-driven-development
> to implement this plan wave by wave. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about approach]

**Tech Stack:** [Key technologies/libraries]

**Spec:** [path to the spec/design doc this plan implements — the plan
argues from the spec, so the spec travels with it; executors read both]

## Global Constraints

[The spec's project-wide requirements — version floors, dependency limits,
naming and copy rules, platform requirements — one line each, with exact
values copied verbatim from the spec. Every task's requirements implicitly
include this section.]

---
```

**Global Constraints is not optional furniture.** It is the block copied
verbatim into every reviewer dispatch as its attention lens (Reviewing a task,
Phase 2). A plan without one leaves every reviewer judging against process
rules alone, with nothing from this project's spec.

**Task structure:**

````markdown
### Task N: [Component Name]

**Files:**
- Create: `db/migrations/0042_add_positions.sql`
- Modify: `services/billing/app/checkout.py`
- Delete: `services/search/app/legacy_index.py`
- Bump: `libs/shared`
- Test: `services/billing/tests/test_checkout.py`

**Interfaces:**
- Depends: 1, 3
- Model: standard
- Consumes: `build_wiring` from Task 1 — its test proves the registration was removed cleanly.
- Produces: [what later tasks rely on — exact function names, parameter and
  return types. A task's implementer sees only its own task; this block is how
  it learns the names and types neighbouring tasks use.]

- [ ] **Step 1: Write the failing test**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "function not defined"

- [ ] **Step 3: Write minimal implementation**

```python
def function(input):
    return expected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

`Depends:` names task numbers or `none` — the leading `- ` is required; it is
what the validator's parser matches on. `Model:` is one of `cheapest` /
`standard` / `most-capable`, chosen per **Model Selection** below.

### No placeholders

Every step must contain the actual content an engineer needs. These are **plan
failures** — never write them:

- "TBD", "TODO", "implement later", "fill in details"
- "Add appropriate error handling" / "add validation" / "handle edge cases"
- "Write tests for the above" (without actual test code)
- "Similar to Task N" (repeat the code — the engineer may be reading tasks out
  of order)
- Steps that describe what to do without showing how (code blocks required for
  code steps)
- References to types, functions, or methods not defined in any task
- A migration filename with an unassigned number (`00xx_add_positions.sql`) —
  see step 3 below for why this one is invisible to the validator

### Building the waves

1. **Compute the file-overlap constraints:** `scripts/plan-tasks PLAN_FILE`
   (no `--validate`) prints every task's declared paths and, at the end,
   `# File overlaps - these pairs MUST NOT share a wave` — the exact task
   pairs that can never share a wave. This is what you assign waves from in
   the next step, not a re-read of the plan prose: the tool exists so path
   overlap is computed once, not judged by eye per task.

2. **Draw task boundaries with the invariant in mind**, applying the four
   heuristics below against the overlap list from step 1 — this decides
   whether work can share a wave at all; it is not something to fix up
   afterward:
   - **Shared infrastructure gets its own early wave.** A file many later
     tasks import — a shared conftest, a shared model module, a schema
     migration — is created once, early, by one task.
   - **One owner per file per wave.** Two tasks that must edit the same file
     go in different waves. Splitting a file by region between concurrent
     tasks is not permitted — it looks disjoint and is not, because the
     merge is textual, not semantic.
   - **Maximise wave width subject to the invariant, never the reverse.**
     Serialising is always available and always correct; forcing
     parallelism is not.
   - **Draw each task's file set to include its blast radius, not its minimum
     edit.** A task that will modify a source file declares that file *and its
     covering test file* — or the test file it will create, when the code is
     new. Two reasons. Tests move with the code in the same commit, and that is
     structural only if the test file is in the declaring task's set. And a
     minimally-drawn file set makes the boy-scout rule unreachable: an
     implementer that must stop and report NEEDS_CONTEXT to delete an unused
     import will simply leave it, and the file gets worse every time it is
     touched.

3. **Assign global singletons at planning time, before any task is
   dispatched:** migration numbers, submodule bump slots (at most one
   task per wave may bump a given submodule — two bumps is a silent
   revert), and lockfile ownership.

   **Migration numbers are per migration home, and homes are never crossed.**
   A project's homes are whatever `scripts/project-config migration_homes`
   prints, each one a separate database with its own number sequence; the same
   number in two different homes is correct, and `--validate`'s Rule 5 treats
   them as separate for exactly that reason. A project that prints nothing has
   no migration homes and skips this singleton entirely.

   **Assigning a migration number is an action, not a note-to-self:** list
   the target home (`ls <home>/ | tail`) and write the concrete next number
   straight into the plan's `**Files:**` block. Rule 5 only catches two tasks
   claiming the *same* number, and has nothing to say about a number nobody
   assigned. A placeholder validates clean and is a planning defect the tool
   structurally cannot see.

4. **Emit the `## Waves` table:**

   ```markdown
   ## Waves

   | Wave | Tasks | Rationale |
   |---|---|---|
   | 1 | 1, 4 | disjoint files; neither depends on anything |
   | 2 | 2, 3, 5 | all depend on 1; disjoint from each other |
   ```

5. **Assign every task's `Model:` tier here, in Phase 1 — not at dispatch.**
   This is the same reasoning as migration numbers in step 3: a decision
   deferred to dispatch is a decision skipped. A wave is dispatched in a single
   message, so a controller choosing at dispatch time is judging every task's
   complexity at once while composing every call; and the Agent tool's `model`
   is *optional*, so the omission costs nothing, says nothing, and silently
   inherits the session's own — usually most expensive — tier. Nothing
   downstream records which model ran, so the mistake leaves no trace to find
   later. Phase 1 already knows each task's file count and character, which is
   exactly what the tier depends on. Rule 7 enforces that the line exists.

6. **Cross-check every `Consumes:` line against `Depends:`:** for each task,
   every task number named in a `Consumes:` line must also appear in that
   task's own `Depends:` line. `--validate` does not check this — the
   string `Consumes:` appears nowhere in `plan-tasks` — so a plan can
   validate green with clause 2 of the invariant already broken: a task
   consuming another's output without declaring the dependency, both
   sharing a wave. This is a manual read, not something to fix in
   `plan-tasks` itself.

### Refactoring: what an implementer may decide alone

Clause 2 of the invariant — no task depends on a sibling in the same wave — is
checked against **declared** dependencies, at planning time, against the
*intended* change. An unplanned refactor creates coupling that did not exist
when the plan was written:

> T1 and T2, same wave, disjoint files. T1 tidies its own file and renames
> `check_user` → `verify_member`. T2 imports `check_user`. Files are disjoint.
> **The merge is clean. The tree is broken.**

That is the "renamed symbol a neighbour imports" case clause 2 exists for —
except clause 2 cannot see it, because nobody declared it.

| Implementer decides alone | Planner decides |
|---|---|
| Dead code, unused imports, local simplification, tightening a block already being edited, test cleanup in an owned test file | Renames, signature changes, moved symbols, changed return types — anything altering what another file sees |

**The test is mechanical:** *would a file outside the task's declared set have
to change?* If yes it is a planned refactor: declare the affected files, or give
it its own wave. **When in doubt, treat the symbol as exported** — a skipped
cleanup costs one untidy block, a wrong call costs a sibling's broken import
surviving a clean merge.

Owning a test file does not license a rename. Drawing the file set to include
the covering test file (Building the waves, step 2) is what lets tests move
with the code; other files still reference the symbol and are not in the set.

### Self-review, then validate

**Run this checklist yourself.** It is not a subagent dispatch — you wrote the
plan, and these three checks need the spec beside it:

1. **Spec coverage.** Skim each section and requirement in the spec. Can you
   point to a task that implements it? List any gaps, and add the task.
2. **Placeholder scan.** Search the plan for every pattern in **No
   placeholders**, above. Fix them.
3. **Type consistency.** Do the types, signatures, and property names used in
   later tasks match what earlier tasks defined? A function called
   `clearLayers()` in Task 3 but `clearFullLayers()` in Task 7 is a bug.

Fix what you find inline; there is no need to re-review your own fixes.

**Then dispatch one plan review** using `plan-reviewer-prompt.md`, on a
standard-or-better model, pointed at the plan and the spec. It reads the plan
as an implementer would and answers one question: could an engineer follow this
without getting stuck? Its calibration is deliberately loose — it flags only
what would cause real problems during implementation — so an Issues Found
verdict is worth acting on rather than arguing with. Fix what it raises, then:

**Validate:** `scripts/plan-tasks --validate PLAN_FILE` (script paths here and
in Phase 2 are relative to this skill's own directory, wherever the plugin is
installed). Fix the plan and re-run until it exits 0 — the printed problems
name the exact rule and tasks involved. A plan that fails validation is not
executable; do not proceed to Phase 2 with it.

## Phase 2 — Execute

Input: a plan that has passed `--validate`. Output: every wave merged into
the plan branch, ready for Final Review.

**Setup, once:**

1. **Create or verify the plan worktree.** You are already in a linked worktree
   when `git rev-parse --git-dir` and `--git-common-dir` differ *and* `git
   rev-parse --show-superproject-working-tree` prints nothing — that second
   check matters because the two dirs also differ inside a submodule, which is
   not a worktree. If you are already in one, use it; do not nest another.

   Otherwise create it under `<worktree-dir>` at the project root, **based on
   `origin/<default-branch>` explicitly**, after a `git fetch` —
   `<worktree-dir>` being the project config's `worktree_dir`
   (`scripts/project-config --scalar worktree_dir`), and `<default-branch>`
   being the project config's `default_branch`, never a name assumed here.
   Every task-worktree path below is spelled `<worktree-dir>/<slug>-t<N>` —
   the same `<worktree-dir>`, never a literal:

   ```bash
   git fetch --no-tags origin <default-branch>
   git worktree add <path> -b <plan-branch> origin/<default-branch>
   ```

   **Naming the base ref is the whole point of spelling this out.** A
   `git worktree add <path> -b <branch>` with **no base ref** starts the branch
   at whatever HEAD the main checkout happens to be on. The main checkout is
   shared by concurrent sessions: its local default branch is routinely ahead
   of the remote one by commits another session has not pushed — in a
   repository whose git hooks commit regenerated output, every merge there
   produces such a commit — and it may not be on the default branch at all.
   Inheriting that silently forks your work off someone else's unpushed WIP —
   and pushing then publishes their work-in-progress under your branch, which
   is a real, observed failure, not a hypothetical one.

   Implementation never begins on the default branch itself.

   `git fetch` writes refs only and is safe under concurrency. `git pull`
   and `git checkout` in the main checkout are not: they move HEAD beneath
   live sessions. Never run either there.
2. **Define `<slug>` once**, using the same rule `wdd-workspace` computes
   internally: the plan file's basename with `.md` stripped — e.g.
   `docs/superpowers/plans/2026-08-02-example.md` → `2026-08-02-example`.
   Always the full basename, never a shortened form: `wdd-workspace` keys
   its task-worktree guard on `"${slug}-t"[0-9]*`, so a shortened slug is a
   pattern the real worktrees never match, and `wdd-workspace` run inside
   one would silently create an orphan workspace instead of refusing.
3. Resolve the workspace: `scripts/wdd-workspace PLAN_FILE` prints the
   absolute path used for briefs, reports, review packages, and the ledger.
   Another plan's directory is never yours to read or write. The workspace is
   git-ignored scratch, so a `git clean -fdx` destroys it; if that happens,
   recover from `git log` and the ledger's surviving copy of the plan name.
   Record `MERGE_BASE=$(git merge-base origin/<default-branch> HEAD)` here too:
   every reviewer dispatch passes it as `[MERGE_BASE]`, the point each
   finding's origin is measured against (Final Review, below).
4. Check for an existing ledger (`<workspace>/progress.md`). If one exists,
   this run is a resume — follow Resuming an interrupted run, below, in
   full before dispatching anything.
5. **Read the plan once**, note its context and Global Constraints, and create
   a todo per task. **If the plan names a Spec, read that too:** the spec is
   the authority the plan argues from, and conflicts inside the plan resolve
   against it. A plan with no reachable spec gets a ledger note saying so —
   rulings made without one are provisional.
6. **Scan the plan for conflicts before dispatching anything**, writing down
   what you checked as you check it:

   - tasks that contradict each other or the plan's Global Constraints;
   - anything the plan explicitly mandates that the review rubric treats as a
     defect (a test that asserts nothing, verbatim duplication of a logic
     block).

   **The scan's output is a table, not a verdict.** One row for every pair of
   tasks that share a file or an interface: the two tasks, what one produces
   against what the other consumes, and what you found. One row for every task:
   whether its own text agrees with itself — the tests it specifies against the
   code it specifies, the files it creates against the files it later touches.
   "The scan is clean" without those rows is not a scan you ran.

   Write the table to the ledger, rule on everything it surfaces before
   execution begins — each finding against the plan text that mandates it — and
   record each ruling beside its row. If the scan is clean, proceed without
   comment. The review loop remains the net for conflicts that only emerge from
   implementation.
7. Re-run `scripts/plan-tasks --validate PLAN_FILE`. A plan can be
   hand-edited between Phase 1 and Phase 2; re-validate before any worktree
   is created.

**Per wave, until every task is complete or escalated:** everything below
runs from the plan worktree unless a command names a different directory
explicitly — task worktrees are always spelled out as
`<worktree-dir>/<slug>-t<N>`. Every subagent dispatch also names its
model explicitly — an omitted model silently inherits the session's most
expensive one. Tiers: see Model Selection, below.

1. **Create each task's worktree.** Capture the branch point once per wave:
   `BASE=$(git rev-parse HEAD)`. Record it — it does not move again until
   step 6 merges this wave, so every review and re-review below reuses this
   same value. Then `scripts/wave-worktree create <slug> <N> "$BASE"`
   (worktree + branch `<slug>-t<N>`), and `scripts/task-brief PLAN_FILE <N>`
   to write its brief. **The report file has no script of its own — name it
   by convention:** the brief's filename with `-brief` replaced by
   `-report` (`task-<N>-brief.md` → `task-<N>-report.md`), in the same
   workspace directory `wdd-workspace` printed. Pass that exact path as
   `[REPORT_FILE]` at every dispatch for this task, including every fix
   round — keeping it stable across rounds is what makes round 2+ append to
   one report rather than each forking its own.

   **A wave holding one task creates no worktree.** The worktree exists to
   isolate concurrent writers; a lone task has no sibling to be isolated from,
   and paying for a worktree, a branch, and an integrator merge to isolate one
   writer from nobody buys nothing. That task is implemented in the plan
   worktree, on the plan branch, and four things below collapse accordingly:
   step 2's restore does not apply (no worktree was created, so no
   `post-checkout` fired), step 6's integrator is not dispatched (the commits
   are already on the plan branch), and steps 7–8 have no worktree to restore
   or remove. **Only the isolation collapses. Every gate stays:** the brief,
   the named model, the task review, the fix loop and its cap, and the ledger
   lines are all exactly as they are for a wide wave. Two details change shape
   rather than disappearing — the third argument to `review-package` is `HEAD`
   rather than a task branch (with the task committing onto the plan branch,
   HEAD genuinely advances past `$BASE`, which is what makes the literal
   `HEAD` correct here and wrong everywhere else in this section), and the
   ledger's dispatch line names the plan branch in place of `<slug>-t<N>`.
2. **Restore immediately — a task worktree is born dirty.** A repository
   that regenerates tracked output from a git hook rewrites those paths in
   every new worktree: `.git/hooks/post-checkout` has no branch gate and
   fires on `git worktree add` itself, before an implementer runs a single
   command — unlike `post-commit`, which such repositories usually gate to
   the default branch so it never fires on a task branch. Right after step 1
   returns, restore every path the project declares regenerable:

   ```bash
   scripts/project-config regenerated_paths |
     xargs -r git -C <worktree-dir>/<slug>-t<N> checkout --
   ```

   so the implementer never sees churn it did not cause. A project that
   declares none prints nothing and `xargs -r` runs nothing — the step
   skips itself, and there is no branch to remember to take. Never widen this
   to a blanket `git checkout -- .`, and never reach for `--force`: telling a
   regenerated artifact from unsaved work is the entire point of the guard
   this protects, and the config is the only thing that knows the
   difference.
3. **Retrying or resuming a task reuses its branch — never delete it.** A
   task re-dispatched after the fix cap (Failure policy, below) or resumed
   after an interrupted wave (Resuming an interrupted run, below) already has a branch
   `<slug>-t<N>`: `wave-worktree remove` deletes worktrees only, never
   branches, and they persist until the final PR. Calling `wave-worktree
   create` again for that task hits its own existing-branch guard (exit 3),
   whose printed remedy is `git branch -D <branch>` — do not follow it: that
   deletes the commits, and with them the fix history and open findings,
   the retry exists to carry forward. Instead bypass `wave-worktree create`
   for this one call and recreate the worktree directly against the
   existing branch.

   **Run `git worktree prune` first, unconditionally, every time** — before
   the `git worktree add` below, whether or not you believe the worktree was
   cleanly removed. `wave-worktree remove` deregisters a worktree cleanly,
   so `prune` is a no-op right after it; a crash that lost the worktree
   directory without going through `wave-worktree remove` (the resume path,
   Resuming an interrupted run, below) leaves git's own registration behind,
   and the `add` below then fails with `fatal: ... is a missing but already
   registered worktree`. A cold controller cannot reliably tell the two
   teardowns apart, so always prune rather than branching on which one this
   is. Do not reach for the `-f` flag git's own error suggests instead:
   `-f` also bypasses git's check that the branch is not checked out live
   somewhere else — a check `prune` never has to bypass, because `prune`
   only ever drops registrations whose directory is already confirmed gone.

   `git worktree prune && git worktree add <worktree-dir>/<slug>-t<N>
   <slug>-t<N>` (no `-b` — checks out the branch that already exists rather
   than creating a new one). Then do the two setup steps `wave-worktree
   create` would otherwise have done, in order: `git -C
   <worktree-dir>/<slug>-t<N> submodule update --init --recursive`
   (a plain `worktree add` does not initialise submodules, so every submodule
   lands empty and any tooling that reads one fails — the exact failure
   `wave-worktree` exists to prevent), then step 2's restore over
   `regenerated_paths` — the `post-checkout` hook that dirties them fires on
   `git worktree add` itself regardless of whether `-b` was given, so a retry
   worktree is born dirty for the same reason a fresh one is.
   `wave-worktree remove <slug> <N>` still works normally afterward; it only
   checks for a worktree at that path.
4. **Dispatch the whole wave's implementers in one message**, one per task
   worktree, using `implementer-prompt.md`, so they run concurrently. **Read
   each task's `Model:` line and pass that tier's model as the dispatch's
   `model` parameter** — the plan already decided this (Phase 1, Building the
   waves, step 5); here you only resolve tier to the concrete model available
   in this session. The parameter is optional and an omission is silent, so
   this is the one step in the wave where doing nothing produces a
   plausible-looking, wrong result.

   **What each dispatch contains**, and nothing else:

   1. one line on where this task fits in the project;
   2. the brief path, introduced as "read this first — it is your
      requirements, with the exact values to use verbatim";
   3. interfaces and decisions from earlier tasks that the brief cannot know;
   4. your resolution of any ambiguity you noticed in the brief;
   5. the report-file path and the report contract.

   **Exact values — numbers, magic strings, signatures, test cases — appear
   only in the brief.** Never make a subagent read the whole plan file. And a
   dispatch prompt describes one task, not the session's history: do not paste
   accumulated prior-task summaries ("state after Tasks 1–3") into later
   dispatches. A real session's dispatch reached 42k characters of which 99%
   was pasted history. A fresh subagent needs its task, the interfaces it
   touches, and the global constraints. Nothing else.

   If an earlier task parked a finding in the area this task touches, carry a
   pointer to that ledger entry in the dispatch. **Record each implementer's
   agent identity from the dispatch result** — fix-loop rounds 1–3 resume that
   agent, and there is no other way to reach it.

   Write `Wave N: dispatched (...)` to the ledger now, **including each task's
   `[tier/model]`**, before any report comes
   back — the resume procedure keys off this line existing from the moment
   of dispatch, not from when a task finishes (Resuming an interrupted run,
   step 1, below). A wave dispatched task-by-task
   across separate messages is serial in effect even though every task has
   its own worktree. **Amend the plan itself with any controller ruling made
   since it was written, before this dispatch — never leave the ruling in the
   ledger alone.** `task-brief` renders the plan's own text, not the ledger;
   a ruling that changes what a task must produce and lives only as a ledger
   line never reaches the subagent that needs it.

   **While the wave runs, do not poll and do not sit in one silent,
   open-ended wait.** While you have local work — ledger updates, packaging
   the next review, reading reports — keep working; child results arrive on
   their own. When you are genuinely idle, wait in bounded stretches (five to
   ten minutes, where the platform allows), and between stretches post one
   line of status and reconcile your live children: list them, and chase any
   that finished without reporting. A bounded stretch keeps nearly all of a
   long wait's efficiency while guaranteeing a stuck or lost child is noticed
   within minutes rather than at the end of the session.
5. As each task finishes, **read its reported status before anything else**
   and handle it (Handling the report, below). Then review it (Reviewing a
   task, below) and run the fix loop until it is green (The fix loop, below).
   Every one of those runs **inside that task's own worktree**.
6. Once every task in the wave is green, dispatch `integrator-prompt.md` to
   merge the wave's branches into the plan branch and run the verification
   the merged tasks name. Once it reports INTEGRATED, write `Wave N:
   integrated (...)` to the ledger — this is the line Resuming an
   interrupted run (step 1, below) looks for to know the wave closed cleanly
   rather than stalled mid-run. **Never integrate a wave while any of its
   tasks has an open Critical or Important finding that is neither fixed,
   nor parked with a ruling at the cap, nor recorded as a `Sweep:` line at
   the cap.**
7. Before removing anything, repeat step 2's restore in each merged task's
   worktree, in case a later hook run re-dirtied it since creation.
8. `wave-worktree remove <slug> <N>` for each merged task — this deletes
   the worktree only. The branch `<slug>-t<N>` is kept and stays on disk
   through every later wave, until the final PR. The plan branch has now
   advanced by this wave.

### Handling the report

An implementer reports one of four statuses.

**DONE** — proceed to Reviewing a task.

**DONE_WITH_CONCERNS** — neither `DONE` nor `BLOCKED`: read the concerns
first. If they bear on correctness or scope, resolve them before review — ask
the implementer, or amend the plan and re-dispatch, same as any other open
question. If they are pure observations ("this file is getting large"), note
them in the ledger and proceed to review as normal. **A report with unread
concerns must never reach the reviewer with its status treated as a plain
`DONE`.**

**NEEDS_CONTEXT** — the implementer needs information it was not given.
Provide it and re-dispatch. Siblings continue unaffected.

**BLOCKED** — assess the blocker:

1. a context problem → provide more context, re-dispatch with the same model;
2. the task needs more reasoning → re-dispatch on a more capable model;
3. the task is too large → break it into smaller pieces;
4. the plan itself is wrong → rule on the correction, ledger it, and
   re-dispatch with the ruling carried in the dispatch.

**Never ignore an escalation, and never force the same model to retry
unchanged.** If the implementer says it is stuck, something has to change.

If an implementer asks a question — before starting or mid-task — answer it
clearly and completely, supply context if needed, and do not rush it into
implementation.

### Reviewing a task

Per-task reviews are task-scoped gates; the broad review happens once, at
Final Review. **Never skip the task review, and never accept a report missing
either verdict** — spec compliance AND task quality are both required. An
implementer's self-review never replaces the task review; both are needed.

Build the diff package: `scripts/review-package PLAN_FILE "$BASE"
<slug>-t<N>`, then dispatch `task-reviewer-prompt.md` with the path it prints.
**Never dispatch a task reviewer without a diff file.**

- **Use the `$BASE` you recorded before the wave was dispatched, never
  `HEAD~1`** — `HEAD~1` silently drops all but the last commit of a
  multi-commit task.
- **The third argument is the task's branch name, never the literal word
  `HEAD`.** Run from the plan worktree, `HEAD` there names the plan-branch
  tip, which during a single wave equals `$BASE` (nothing has merged into the
  plan branch yet) — so a literal `HEAD` silently produces an empty package:
  exit 0, "0 commit(s)," no warning, and the reviewer then reviews nothing.
  (The one exception is a width-1 wave, where there is no task branch and the
  task commits onto the plan branch — per-wave step 1.)
- **Reviewer inputs are three paths plus one block:** the same brief file, the
  report file, and the review package — plus the global constraints that bind
  the task, and `[MERGE_BASE]`, against which the reviewer tags each finding
  `introduced` or `pre-existing`.
- **The global-constraints block is the reviewer's attention lens.** Copy the
  binding requirements verbatim from the plan's Global Constraints section or
  the spec: exact values, exact formats, and the stated relationships between
  components ("same layout as X", "matches Y"). The reviewer's template already
  carries the process rules — YAGNI, test hygiene, review method — so the
  constraints block is for what THIS project's spec demands.
- **Do not add open-ended directives** like "check all uses" or "run race tests
  if useful" without a concrete, task-specific reason.
- **Do not ask a reviewer to re-run tests the implementer already ran** on the
  same code — the implementer's report carries the test evidence.
- **Never pre-judge findings for the reviewer.** Never instruct a reviewer to
  ignore or not flag a specific issue; let it raise the finding and resolve it
  in the review loop instead. This is more load-bearing here than in a serial
  workflow: the Automation boundary (below) grants the controller authority to
  resolve a reproduction-backed finding itself, which is a standing temptation
  to tell the reviewer not to raise it and skip the loop entirely. If a
  dispatch prompt contains "do not flag," "don't treat X as a defect," "at most
  Minor," or "the plan chose" — stop: that is pre-judging, usually to spare
  yourself a review loop.

The reviewer may report **⚠️ Cannot verify from diff** items — requirements
living in unchanged code or spanning tasks. These do not block the rest of the
review, but **you resolve every one of them yourself before the task closes**:
you hold the plan and the cross-task context the reviewer lacks. It is never
suppressed and never decided by the reviewer itself. If you confirm an item is
a real gap, treat it as a failed spec review — it enters the fix loop with the
other findings.

### The fix loop

The loop triggers on spec ❌, any `introduced` Critical or Important finding,
or a ⚠️ item you confirmed as a real gap. Three routes leave it before it
starts:

- **Minor findings never enter the loop.** Record each in the ledger as you go,
  as a `Sweep:` line carrying its origin tag (Ledger, below) — add `(late)` when
  the task is a sweep task. The next full-branch review routes that list: during
  the spec's waves that is review #1, and a small item is fixed in this run's
  sweep rather than filed (Final Review, below). A roll-up nobody reads is a
  silent discard.
- **A `pre-existing` finding, any severity, never enters the loop:** record it as
  `Sweep: pre-existing — <finding> — from Task <N> review — evidence: <evidence>`,
  adding `(late)` after `pre-existing` when the task is a sweep task; the next
  full-branch review routes it. The task did not cause it, so the task's loop is
  the wrong place to fix it. The evidence rule still holds (Final Review, Where a
  finding came from): a `pre-existing` tag with no evidence, or evidence that
  does not hold, is treated as `introduced` — and then it does enter the loop.
- **A finding labelled plan-mandated** — or any finding that conflicts with
  what the plan's text requires — is yours to rule on: weigh the finding
  against the plan text, decide with the spec as the binding authority, and
  ledger the ruling before acting on it. Do not dismiss a finding because the
  plan mandates it, and do not dispatch a fix that contradicts the plan without
  a recorded ruling.

Everything else enters the loop. A fix round is one fix dispatch plus one
scoped re-review. **Five rounds maximum per task.**

**Rounds 1–3 — resume the original implementer.** Send it the open findings
verbatim; its context is intact, so it knows the task, the code, and its own
choices. If the original implementer is unreachable — always true after a
session death, since a subagent's context does not survive one — dispatch a
fresh implementer carrying the brief path, the report-file path, and the open
findings. **Name its model explicitly even here:** at rounds 1–3 the
replacement is forced by unreachability, not by the cap, so it is not an
escalation — use the same tier the task's implementer dispatch would otherwise
use at that round (Model Selection, below). An unnamed model silently inherits
the session's own, often most expensive, tier.

**Rounds 4–5 — dispatch a fresh implementer one model tier above the one that
got stuck**, with the brief path, the report-file path, the open findings, and
this framing: *"A prior implementer attempted this task [N] times; you own it
now. Read the report file for what was tried."* A loop that survives three
resumes usually means the implementer cannot see its own problem — fresh eyes
and a capability bump in one move.

**Every round, either way:** capture the branch's tip
(`git rev-parse <slug>-t<N>`) right before dispatching each review or
re-review — that snapshot is `FIX_BASE_SHA` for the following round. The
implementer fixes, re-runs the tests covering the amended code, appends its fix
report to the same report file, and returns the short contract. **Before
re-dispatching the reviewer, confirm the fix report contains the covering
tests, the command run, and the output**; dispatch the re-review once all three
are present. Name the covering test files in the fix message — a one-line fix
does not need the whole suite. The report file is the persistent memory either
way, which is what makes the loop survivable rather than merely restartable.

**The re-review is scoped:** `scripts/review-package PLAN_FILE "$FIX_BASE_SHA"
<slug>-t<N>` (third argument still the branch name, never literal `HEAD`) plus
`re-review-prompt.md`, with the findings list, the brief, the report file, and
`[MERGE_BASE]`.
The re-reviewer verdicts each finding ADDRESSED or NOT ADDRESSED and flags new
breakage in the fix diff only. New Critical/Important breakage in the fix diff
joins the open findings list. Out-of-scope observations go to the ledger as
`Sweep:` lines, each with its origin tag — they never extend the loop.

This is why a task's worktree is not removed until after the merge, not merely
until the implementer's first report.

**Never fix findings yourself.** The controller never writes code: controller
edits pollute the coordination context and skip review entirely.

**The breaker.** When round 5's re-review still leaves findings open, stop
dispatching and adjudicate each open finding yourself — you hold the plan and
the cross-task context the reviewer lacks:

- **The reviewer is wrong, or the point is contestable** → park it:
  `Task <N>: parked — <finding> — Ruling: <why the code stands>`. Review #1
  sees both sides: its sweep list carries every parked line, and it routes
  each one like any other item (Final Review, below).
- **Real, but nothing downstream builds on it** → do not park it. Record it as
  `Sweep: introduced must-fix — <finding> — from Task <N> review` (or as a
  `pre-existing` line with its evidence, when it was already there). A defect
  the branch caused is fixed on the branch; it never leaves as a follow-up.
- **Real and load-bearing** — a later task builds on it, or it reveals a plan
  defect → rule on the smallest change that unblocks the dependent work, ledger
  it as `Task <N>: Ruling: <finding> — <what you decided and why>`, and carry
  it into the next task's dispatch. **Parking a structural failure silently
  lets every dependent task build on it.**

**Adjudicate only at the cap.** Adjudicating earlier to end a loop is
pre-judging with a different name. Every adjudication is a ledger entry; a
silent discard is forbidden.

### Completing a task

When the review comes back clean — or, at the cap, every open finding is parked
with a ruling or recorded as a `Sweep:` line — append the completion line to the
ledger:

- `Task <N>: complete (commits <base7>..<head7>, review clean, branch <slug>-t<N>)`
- `Task <N>: complete (commits <base7>..<head7>, <K> parked, branch <slug>-t<N>)`
  after a tripped breaker. `<K>` counts parked findings only; a finding the
  breaker recorded as a `Sweep:` line is visible as that line instead.

Then mark the todo complete.

## Model Selection

**For implementers the tier is not chosen here** — it is read from the task's
`Model:` line, assigned in Phase 1 and enforced by `--validate` Rule 7. A wave
is dispatched in one message, where choosing means judging every task at once
and the cheapest move is to omit the parameter entirely. **Dispatch transcribes
the plan's tier; it does not re-derive it.** If a tier looks wrong while
dispatching, fix the plan and re-validate — do not silently substitute, or the
ledger will record a tier the plan does not contain.

The tiers below are what a planner picks *from*, and what the roles with no plan
line — reviewer, integrator, plan review, final review, fix-loop escalation —
are chosen by at dispatch. Use the least powerful model that can handle the role:

- **Implementer, mechanical task** (isolated functions, a clear spec, 1–2 files, or the plan
  supplies the exact code to transcribe): cheapest tier.
- **Implementer, integration or judgment task** (multi-file coordination, pattern matching,
  debugging): standard tier.
- **Implementer, architecture or design task**: most capable available model.
- **Task reviewer and re-reviewer**: scale to the diff's size, complexity, and risk — a small
  mechanical diff does not need the most capable model, a subtle concurrency change does. Scoped
  re-reviews of small fix diffs take a cheap-to-mid tier.
- **Integrator**: standard tier, always — the role merges and verifies, it never authors code.
- **Plan review** (Phase 1): standard tier or better — it is reading for gaps, not writing.
- **Full-branch reviews** (#1, #2, #3): the most capable available model, not the session default
  (see Final Review, below).
- **Fix-loop escalation (rounds 4–5):** at least one tier above the implementer that got stuck.

**Always name the model explicitly at dispatch.** An omitted model inherits the session's own model
— often the most capable and most expensive — which silently defeats every rule above.

**Turn count beats token price.** Wall-clock and context cost scale with how many turns a subagent
takes, and the cheapest models routinely take 2–3× the turns on multi-step work, costing more
overall. **Use a mid-tier model as the floor for reviewers, and for implementers working from prose
descriptions.** Reserve the cheapest tier for what it is actually good at: a task whose plan text
contains the complete code to write, where implementation is transcription plus testing, and
single-file mechanical fixes.

## Ledger

`<plan-worktree>/.pitcall/wdd/<plan-slug>/progress.md`, first line naming the plan file.

**The ledger exists because conversation memory does not survive compaction.**
In real sessions, controllers that lost their place have re-dispatched entire
completed task sequences — the single most expensive failure observed. It is
your recovery map: the commits it names exist in git even when your context no
longer remembers creating them. After a compaction, trust the ledger and
`git log` over your own recollection, never the reverse.

The `Task <N>: complete` line is what carries that property, so its wording is
fixed: after a context loss, a task carrying that line is done and must not be
re-dispatched. Wave frames wrap it. A fix round's line is one normative format:
`Task <N>: fix round <R>/5 (<A> addressed, <O> open — <finding text>)`, the
`— <finding text>` clause present whenever `<O>` is nonzero and omitted when it
is zero. This is the only fix-round format — do not also encode a commit range
into this line; `git log BASE..<task-branch>` is the source for committed
progress.

Every ruling is a ledger line, written as
`Ruling: <what you decided> — <why> — <what it costs if wrong>`.

**The sweep's lines** (Final Review, below) are anchored at the start of the line and read by
`scripts/wdd-ledger`, the one parser of these formats — the example below is fed through it by
the test suite, so a format changed here and not there fails a test rather than a run:

| Line | Written |
|---|---|
| `Sweep: <introduced\|pre-existing>[ must-fix][ (late)] — <one-liner> — from <src>[ — evidence: <evidence>]` | for a finding not fixed in its own loop. `<src>` is `Task <N> review`, `Task <N> re-review`, `final review #<k>`, or `final review #<k> re-review` (the scoped re-review of the fix that follows review #k); `evidence` is required for `pre-existing`; `(late)` marks one raised during the sweep or after it |
| `Sorting: review #1 at <sha7>` | after the last `Route:` line |
| `Route: <must-fix\|sweep\|ask\|issue\|note>[ #<N>[ full\|part]] — <one-liner>` | per item, from review #1's routing; an `issue` route without `#<N>` means no duplicate was found |
| `Owner: <one-liner> — <decision> (<YYYY-MM-DD>)` | per `ask` item, once your human partner answers |
| `Amendment <K>: written[ (v<N>)]` | once the spec PATCH succeeds |
| `Sweep plan: waves <A>-<B>` | once the sweep is planned and validated |
| `Final review #<k>: <CLEAN\|MUST-FIX\|BLOCKED> at <sha7>` | after each full-branch review |
| `Fixed: <one-liner>[ — closes #<N>\| — part of #<N>]` | when a `sweep` or approved `ask` item's task completes |
| `Seen: #<N> — <path:line>` | for an `issue` item that matched an open issue |
| `Filed: #<N> — <title>` | after filing an `issue` item |
| `Note: <one-liner> — <reason>` | for a `note` item |

Issue numbers appear only from sorting onward; a `Sweep:` line never carries one.

**Stop markers are exactly two lines:** `Task <N>: BLOCKED` and `Final review #<k>: BLOCKED`. The
word anywhere else in the ledger is prose, and `wdd` ignores it. A stop marker is resolved by a
later line for the same subject — `Task <N>: complete`, or a `Final review #<j>:` line with
`j > k` — **never by editing the ledger**. That edit is the one repair that must not happen: the
ledger is the run's evidence.

```ledger
# WDD ledger — plan: docs/superpowers/plans/2026-08-02-example.md
Wave 1: dispatched (BASE a1b2c3d, T1 @ 2026-08-02-example-t1 [cheapest/haiku], T4 @ 2026-08-02-example-t4 [standard/sonnet])
Task 1: complete (commits a1b2c3d..d4e5f6a, review clean, branch 2026-08-02-example-t1)
Task 4: fix round 1/5 (2 addressed, 1 open — retry does not clear the idempotency key on failure)
Task 4: fix round 2/5 (1 addressed, 0 open)
Sweep: introduced — the retry log line repeats the request id — from Task 4 review
Task 4: complete (commits e5f6a7b..b7c8d9e, review clean, branch 2026-08-02-example-t4)
Wave 1: integrated (merge 9f2c1ab, pytest 264 passed / 22 skipped, worktrees removed)
Wave 2: dispatched (BASE 9f2c1ab, T2 @ 2026-08-02-example-t2 [most-capable/opus], T3 @ 2026-08-02-example-t3 [standard/sonnet], T5 @ 2026-08-02-example-t5 [cheapest/haiku])
Sweep: pre-existing — the config loader swallows a malformed port — from Task 3 review — evidence: app/config.py:88 at 0c4e2aa
Task 5: BLOCKED (the retry also failed — the plan's schema lacks the column the spec requires)
Ruling: add the column to Task 5's declared migration — the spec requires it — one wasted wave if wrong
Task 5: complete (commits 3d4e5f6..7a8b9c0, review clean, branch 2026-08-02-example-t5)
Task 2: complete (commits 9f2c1ab..1b2c3d4, review clean, branch 2026-08-02-example-t2)
Task 3: complete (commits 9f2c1ab..5e6f7a8, review clean, branch 2026-08-02-example-t3)
Wave 2: integrated (merge 4b5c6d7, pytest 281 passed / 22 skipped, worktrees removed)
Final review #1: MUST-FIX at 4b5c6d7
Route: must-fix — Task 2's handler drops the tenant filter on retry
Route: sweep — the retry log line repeats the request id
Route: sweep #31 full — the config loader swallows a malformed port
Route: ask — two defensible names for the exported flag
Route: issue — the lane exit status hides a failed bringup
Route: issue #44 — the receipt path is computed twice
Route: note — a dormant quoting edge in the legacy export
Sorting: review #1 at 4b5c6d7
Owner: two defensible names for the exported flag — keep --notes (2026-08-03)
Amendment 1: written (v3)
Ruling: amendment 1 sweeps the log line, #31 and the flag name — each has a reproduction and fits one task — one sweep wave if wrong
Sweep plan: waves 3-3
Wave 3: dispatched (BASE 4b5c6d7, T6 @ 2026-08-02-example-t6 [standard/sonnet], T7 @ 2026-08-02-example-t7 [cheapest/haiku])
Sweep: pre-existing (late) — the export path ignores the locale — from Task 7 review — evidence: app/export.py:40 at 0c4e2aa
Task 6: complete (commits 4b5c6d7..6f7a8b9, review clean, branch 2026-08-02-example-t6)
Fixed: the config loader swallows a malformed port — closes #31
Fixed: the retry log line repeats the request id
Task 7: complete (commits 4b5c6d7..0a1b2c3, review clean, branch 2026-08-02-example-t7)
Fixed: two defensible names for the exported flag
Wave 3: integrated (merge 8e9f0a1, pytest 286 passed / 22 skipped, worktrees removed)
Final review #2: CLEAN at 8e9f0a1
Filed: #52 — the lane exit status hides a failed bringup
Seen: #44 — app/receipts.py:12
Note: a dormant quoting edge in the legacy export — no caller reaches it
Note: the export path ignores the locale — no locale but en is configured
```

`BASE` is in the dispatch line because a resumed session cannot otherwise recover it, and every
review and re-review needs it. If an older ledger lacks it, derive it:
`git merge-base <task-branch> <plan-branch>`.

`[tier/model]` records the plan's declared tier **and the concrete model it resolved to**, per
task. Both halves earn their place: the tier alone cannot be checked against what ran, and the
model alone loses the intent it was meant to satisfy. Without this line a wave that quietly
dispatched everything on the session's own model is indistinguishable from one that followed the
plan — git records the commits, never the model that wrote them, so there is otherwise no artifact
anywhere that could answer "which model ran Task 3?". A tier here that disagrees with the plan is
a real finding, not a formatting slip.

Open findings are recorded in the fix-round line **in full, not by reference**: `review-package`
writes only the diff, and the reviewer's findings otherwise live only in the controller's own
context — exactly what a crash destroys. A resumed re-review has no other source for
`re-review-prompt.md`'s `[FINDINGS]`, which requires them verbatim.

**Recovery rule:** the ledger's own marker decides what resumes, not commit presence — a task
carrying a `Task <N>: complete` line is done and must never be re-dispatched; every other task named
in an open wave's `dispatched` line resumes, including one that already has commits on its branch.
Full mechanics: see Resuming an interrupted run, below.

## Resuming an interrupted run

A dropped connection, an exhausted quota, or an API error kills the session mid-wave. Everything
durable survives — git, the ledger, the briefs, the reports, the worktrees. What does not is your
place in the run and every live subagent's reasoning.

**Resume is automatic.** Given a plan file you resolve the workspace and read the ledger before
anything else; an open wave means this run is a resume, not a fresh start. Do not require a flag to
say so — after a crash the natural act is re-running the same command, and if that restarts instead
of resuming it re-dispatches completed tasks, which is the most expensive failure on record.
`rm -rf <workspace>` is how someone asks for a genuinely fresh start.

**One case is not a recovery at all.** Quota exhaustion may leave the session alive and merely unable
to dispatch. There is no state to rebuild and resuming immediately fails in the same place. Say so
and stop; do not spend the retry budget dispatching into a wall.

Run this before dispatching anything.

**0. Read the ledger and check it is intact.** Its first line names its plan; a ledger naming a
different plan belongs to another run and is never yours to read or write.

A crash during an append can leave a truncated final line with no trailing newline — e.g. `Wave 1:
integrated (merge 9f2c1ab, pytest 26`. Two rules, one per direction, because getting either wrong
corrupts the ledger permanently:

- **Read:** a line counts only if the file's content up to and including it ends in a newline —
  check the last byte before trusting the last line. Do not use a substring or "contains the
  marker" test: the fragment above is a literal *prefix* of the well-formed `Wave 1: integrated`
  line, so it matches one anyway and produces a false positive that the wave is already integrated.
- **Write:** before writing anything, truncate the fragment from the file. Appending your new line
  after an unterminated one splices it onto the fragment, producing a newline-terminated line that
  *begins* with the marker text — which every later read then believes. One crash plus one resume
  is enough to permanently convert a recoverable ledger into one that reads as integrated forever.

Without a plan file (`continue`), scan for ledgers across every worktree of this repo, not one
fixed root: `wdd-workspace` resolves the workspace from `git rev-parse --show-toplevel`, which
inside a plan worktree — the normal place to run this skill — is the
*worktree's* root, not the main checkout's. A scan of only `<repo-root>/.pitcall/wdd/` from the
main checkout then finds nothing and reports "nothing to resume," the one answer that invites a
fresh start over a run that is in fact resumable. Enumerate roots with `git worktree list
--porcelain` and glob `<root>/.pitcall/wdd/*/progress.md` under each. Ledgers with an open
wave: exactly one, resume it; none, nothing to resume; more than one, stop and ask which, because
guessing picks somebody's other run.

**1. Find the open wave** — a `Wave N: dispatched` with no matching `Wave N: integrated`.

- **Found one** → this wave was interrupted; resume it, starting at step 2 below.
- **None, no `Final review #1:` line yet, and the plan's `## Waves` table names a wave after the
  highest `Wave N: integrated` in the ledger** → the crash landed between that wave's integration
  and the next wave's dispatch, a wide window since the controller does teardown and next-wave
  planning there. Resume at that next wave: continue through steps 2–3 below (both are cheap and
  safe regardless), skip step 4 (nothing in this wave has been dispatched yet, so there is no
  per-task state to recover), then dispatch it fresh per Phase 2's per-wave step 4. Never
  re-dispatch a wave that already carries its `integrated` line. **Once a `Final review #1:` line
  exists this bullet no longer applies:** every wave the table names past that point is a sweep
  wave, and sweep waves reach the table while the sweep is still being planned — before it is
  validated and before `Sweep plan:` is appended. Dispatching one from the table alone would run an
  unvalidated sweep. The next bullet decides instead.
- **No open wave, and either every wave in the table is integrated or a `Final review #1:` line
  exists** → the crash landed in Final Review or the sweep, and its phase markers say where:
  `Sorting:` present → sorting is done; `Amendment <K>: written` → the spec is amended;
  `Sweep plan:` → the sweep is planned and validated, and its waves resume like any other — a
  sweep wave is dispatched only once this marker exists; `Final review #<k>:` → that review
  finished. Each marker is appended only after its step completes, so a resumed session never
  repeats a step whose marker exists — and repeats, whole, one whose marker is missing.
- **No ledger at all** → not a resume; start normally.

**2. Clear an interrupted merge.** Detect one with `git rev-parse -q --verify MERGE_HEAD` in the
plan worktree — a printed SHA means a merge is in progress. Abort it: it was authored by an agent
that never reported and that nothing reviewed.

A partly-integrated wave needs no special handling: the integrator may have merged two of three
branches before dying. Merging a branch that is already an ancestor reports "Already up to date" and
changes nothing, so integration is idempotent — re-dispatch it over the wave's full branch list and
let git skip what already landed. Do not try to work out which merges happened.

**3. Re-validate the plan** with `scripts/plan-tasks --validate PLAN_FILE`. It may have been
hand-edited between sessions, and the check is cheap.

**4. Recover per-task state** for every task in the open wave with no `Task <N>: complete` line:

| Source | Yields |
|---|---|
| Ledger `Task <N>: complete` | Done. Never re-dispatch. The only marker that decides it. |
| Ledger `Task <N>: fix round <R>/5 (… <Y> open — …)` | The round it reached and the findings still open. Continue at round R+1 carrying those findings. |
| The `Wave N: dispatched` line, else `git merge-base <task-branch> <plan-branch>` | `BASE` |
| `git log BASE..<task-branch>` | Committed progress |
| `git status --porcelain` in the task worktree, minus hook-owned paths | Uncommitted in-flight work |
| The task's own test command (named in its brief), run in its worktree | Whether the tree is coherent, and where it stopped |

A task interrupted **mid-fix-loop** is the case most easily got wrong: it has commits, no `complete`
line, and a round counter naming findings still open. Restarting it at round 1 discards review work
already accepted and resets the cap that stops an unproductive loop.

**Hook-owned paths are not a signal, and cannot be inferred.** `post-checkout` has no gate on
*which* branch, and fires on `git worktree add` itself, so every task worktree is born dirty before
an implementer runs a single command. It does gate on checkout *type* — branch vs. file, git's own
third `post-checkout` argument — which is why the documented restore, `git checkout -- <path>` (a
file-level checkout), does not re-trigger it, while `git worktree add` (a branch-level checkout)
does. Reading "uncommitted changes exist" without excluding hook-owned paths concludes every
interrupted task was mid-edit. A path is hook-owned when the repository declares it regenerable,
which is exactly the `regenerated_paths` list Phase 2's per-wave step 2 restores from — read it the
same way, and exclude nothing beyond it. Nothing in a diff distinguishes a hook's output from an
implementer's work, and guessing permissively destroys real work.

**A missing worktree is not a missing task.** If the session died between teardown and the ledger
line, only the branch survives. Degrade to commits-only and say so in the dispatch.

**5. Resume dispatching.** For every task recovered in step 4, dispatch a fresh implementer per
`implementer-prompt.md` — the original implementer's context does not survive a session death. The
dispatch **must state explicitly that a prior attempt was interrupted**: that sentence is what
activates "If You Are Picking Up an Interrupted Task" in the template, which is skipped by default —
a dispatch that omits it gets an implementer that believes its worktree is clean and silently
overwrites the dead agent's work. Carry:

- the brief path and the report-file path — the same files the original implementer used; the
  report file is the only memory that survived;
- the commits from `git log BASE..<task-branch>`;
- the uncommitted paths from step 4's `git status --porcelain`, with hook-owned paths already
  excluded;
- the suite result observed in that worktree.

**Where the worktree itself was lost** (the "missing worktree" case, above), say **commits-only**
instead of the last two — there is no worktree to have run `git status` or the suite in. Re-create it
first via the retry path in Phase 2's per-wave step 3 (reuse the existing branch, never delete it);
the `git worktree prune` immediately before that step's `git worktree add` is not optional here — a
worktree lost to a crash is exactly the "missing but already registered" state `prune` exists to
clear.

**This contract is not only for the implementer.** When this task is later reviewed, its
`[GLOBAL_CONSTRAINTS]` (`task-reviewer-prompt.md`) must also carry the sentence "this task was
resumed after an interruption" — the only signal that fires the reviewer's own check 6 (Refactoring
and Test Hygiene) for a silent discard. `task-brief` renders identical text for a fresh and a
resumed dispatch, so nothing else carries this forward.

## Failure policy

| Situation | Action |
|---|---|
| A spec task hits the 5-round fix cap with load-bearing findings open | Green siblings merge. The task is re-dispatched in the next wave — fresh implementer, one model tier up, carrying the brief, the report file, and the open findings. Its dependents wait one wave. |
| The retry also fails | `Task <N>: BLOCKED` in the ledger, stop, report with the findings, the plan text they collide with, and the fix history. |
| The integrator hits a merge conflict | Hard BLOCKED. Under the core invariant this cannot happen unless the plan violated its own guarantee, so it is a planning defect, not a merge to resolve. Re-validate the plan and surface it. |
| A task's diff touches an undeclared path | Spec failure for that task; it enters the fix loop. If the file is genuinely needed, the plan is wrong — fix the plan and re-validate before continuing. |
| An implementer reports NEEDS_CONTEXT | Answer and re-dispatch. Siblings continue unaffected. |
| A sweep task cannot finish — its 5-round cap, or its item outgrows admission | It is never merged, and no wave runs after the sweep range. A `sweep` or approved `ask` item is re-routed `issue`, its amendment line struck through; a `must-fix` item becomes `Sweep: introduced must-fix (late)` for review #2's fix dispatch. Remove its worktree (`scripts/wave-worktree remove <slug> <N>`, which keeps the branch), rename the branch out of the task namespace with `git branch -m <slug>-t<N> <slug>-dropped-t<N>`, push it (`git push origin <slug>-dropped-t<N>`) so the re-routed issue, which names the branch and its tip sha, links to something that resolves, and record `Ruling: <item> dropped, branch <slug>-dropped-t<N> at <sha7> — <why it could not finish> — <what it costs if wrong>`: `wdd` refuses every unmerged `<slug>-t<N>`, and merging the branch to quiet it would land the work this row exists to keep out. `cleanup` leaves the renamed branch alone — it is the only copy of the attempt. The retry-in-the-next-wave above does not apply. |
| Review #3, or any later review, leaves a must-fix open | `Final review #<k>: BLOCKED at <sha7>`; stop, and hand over the open findings, every review report, and the fix history. |

## Automation boundary

**The human owns the spec. That is the boundary — there is not a second one.** From an approved
spec the skill runs unattended through to a **merged** PR: it plans, executes, **runs the lane
itself** (Phase 3), and merges with `wdd merge`, whose bound is a lane receipt for the exact commit
rather than a human's attention. Within that, it **interrupts only when human input is genuinely
needed**.

*Earlier revisions of this section gave the human a second gate — "the human owns the lane run
that validates the branch… authorising the validation is not [automated]". That is retired.* It
contradicted the argument made three paragraphs below: a merge needs no permission **because** a
receipt is stronger evidence than asking — and the lane run is what produces that receipt, so
gating it moved the interrupt one step upstream of where this section had just removed it. Worse,
it was load-bearing nowhere else: `lane.py` has no prompt and no interactive path, and Phase 3
listed no lane step, so a session following this skill literally reached `merge`, was refused for
a missing receipt, and had been given no step that would have produced one.

**Do not pause to check in between tasks or between waves.** Execute the plan
through to Final Review. "Should I continue?" prompts and progress summaries
spend your human partner's attention and buy nothing — they asked for the plan
to be executed, so execute it. A running plan does not wait on a human:
conflicts, ambiguities, plan defects, a cap you would have asked to exceed —
decide them. The spec is the binding authority, the plan is its argument, and
your judgment settles what neither answers. **Record every decision in the
ledger** and keep going. A wrong ruling costs rework your human partner can see
and undo; a session parked on a question costs their whole day and buys nothing.

**Resolve autonomously, record the ruling:**
- A reviewer finding backed by a **working reproduction**, including one that contradicts the plan's
  own text. The plan is a means, not the authority; a reproduction outranks it. Every such ruling is
  a ledger entry and is handed to the final whole-branch review.
- The same test runs in reverse: a finding you believe is wrong is rebutted by
  code or a test that demonstrates the behaviour is correct, not by assertion.
  Record that too — a rebuttal without a demonstration is a dismissal.
- This test **supersedes `task-reviewer-prompt.md`'s own framing for plan-mandated findings**
  ("the human decides"): under an earlier revision that human-decides default fired 4 times in 4
  tasks and would have deadlocked an unattended run, and all four arrived with working
  reproductions — exactly the case the rule above resolves autonomously instead.
- Anything the fix loop closes within its round cap.

**Interrupt and ask:**
- A finding with **no reproduction** that would require contradicting the plan.
- A genuine design fork — two defensible resolutions with materially different outcomes.
- A BLOCKED the controller cannot resolve, or a validation failure implying the spec is wrong.
- A plan so broken that every path forward is a guess.
- The **batched question** after review #1 routes the sweep list (Final Review, below): every
  `ask` item in one message, once per run. It is the one planned interrupt.
- A ruling that would **weaken** an authentication, tenancy, or secrets check, or any action that is
  destructive or irreversible. This is scoped to the controller's own decisions, not to task
  content: in a codebase where nearly every task touches tenancy, "the diff mentions `org_id`" is
  not a trigger. "I am about to rule that a missing `org_id` filter is acceptable" is.

**Running the lane, pushing to a shared branch, and merging are not on that
list, deliberately.** They would be, for a workflow whose only bound was a
human's attention. Here `wdd merge` is bound by a lane receipt pinned to the
exact commit (Phase 3), which is a stronger check than asking — so this skill
validates the branch and lands the PR itself.

Two things about `lane run` to state rather than to stop for. It takes a
**machine-wide mutex**, so it can queue behind another session and wait its
turn — that is scheduling, not a decision. And it runs the project's `bringup`
and `validate`, which in some projects means a live stack with real outbound
capability; a project that must not be reached unattended constrains that in
its own config (`validate`, `outbound_allowlist`), where the constraint is
visible in a diff, rather than in a habit of asking.

**Artifacts hand over as file paths, never pasted through the controller's
context.** Everything you paste into a dispatch prompt, and everything a
subagent prints back, stays resident for the rest of the session and is re-read
on every later turn.

## Final Review

After the last spec wave merges, the run ends with full-branch reviews and **one sweep** of what
it found. Every finding that would once have left as a follow-up issue — a Minor, a finding on
code the branch did not write, a finding still open at a task's cap — has been accumulating as a
`Sweep:` line (Ledger, above). The small ones are fixed here, in this run; only what genuinely
does not fit leaves as an issue.

```
spec waves
  -> review #1: whole branch, from scratch (always)              nothing to fix? -> Phase 3
  -> the batched question, the amendment, the sweep waves
  -> review #2: what changed since #1, whole branch as context   CLEAN?          -> Phase 3
  -> one fix dispatch -> one scoped re-review
  -> review #3: what changed since #2, whole branch as context   CLEAN?          -> Phase 3, else BLOCKED
```

### Where a finding came from

Every finding carries one origin tag, measured against `MERGE_BASE` — the branch's merge-base
with `origin/<default-branch>`, not a task's `BASE`, so a defect an earlier task on this branch
added is still `introduced`:

- **`introduced`** — this branch caused it.
- **`pre-existing`** — it was already there. It carries evidence: the offending lines as
  `<path>:<line> at <merge-base7>`, or a reproduction that fails at the merge-base, with its
  command and output. Check line evidence with one `git show <merge-base>:<path>` before acting
  on the tag. **No evidence, or evidence that does not hold, means `introduced`** — the expensive
  mistake is a defect this branch caused leaving it labelled as old.

### Review #1, and sorting

1. `git fetch --no-tags origin <default-branch>`, then
   `MERGE_BASE=$(git merge-base origin/<default-branch> HEAD)`, then
   `scripts/review-package PLAN_FILE "$MERGE_BASE" HEAD` to build the whole-branch package.
   **The remote ref, never the local one** — the shared checkout's copy of the default branch
   drifts both ways (ahead by commits nobody pushed, behind by merges nobody pulled), and a
   merge-base taken against a drifting ref scopes the review to the wrong range, silently
   reviewing too much or too little.
2. Write `<workspace>/open-issues.json` with
   `gh issue list --state open --limit 500 --json number,title,labels`. Rewrite it before every
   full-branch review: every one can route `issue`, and each checks for duplicates first.
3. Dispatch `final-reviewer-prompt.md` on the most capable available model — not the session
   default — as review #1: `[SINCE_SHA]` is `$MERGE_BASE`, `[REVIEWED_SHA]` is
   `git rev-parse HEAD`, `[REPORT_FILE]` is `<workspace>/final-review-1.md`, `[SWEEP_LINES]`
   carries every `Sweep:` line **and every `Task <N>: parked` line** in the ledger, copied in
   full, and `[OPEN_ISSUES_FILE]`, `[CLAIM_LABELS]` (`status_labels.ongoing` and
   `status_labels.in_review`, whichever are set, or `none`) and
   `[SPEC_ISSUE]` are filled as the template's placeholder list says. A parked line is the
   breaker's ruling that a reviewer was wrong; this is its whole-branch second look, and it is
   routed like any other item — `note` when the reviewer agrees with the ruling. The reviewer
   writes its report there, verdict header first; append `Final review #1: <verdict> at <sha7>`.
4. Transcribe every item's route as a `Route:` line, then append `Sorting: review #1 at <sha7>`.
   Check mechanically, and nothing more: every item has a route, no `introduced` item is routed
   `issue`, and every `pre-existing` tag's evidence holds. You do not re-judge routes.

| Route | When |
|---|---|
| `must-fix` | `introduced` and Critical/Important, or already marked `must-fix`. It takes precedence: a fix that is a design fork goes through the Automation boundary's design-fork interrupt, not `ask`. |
| `sweep` | all three hold: a reproduction or a check that fails today; fits one task with declared paths; not a design fork. A matched `#N` is `full` or `part`. |
| `ask` | the first two sweep conditions hold, but it is a design fork |
| `issue` | `pre-existing`, real, and fails the sweep conditions; it names its duplicate `#N`, or `none` |
| `note` | dormant or low value; or it matches an issue another session claimed — one carrying `status_labels.ongoing` or `status_labels.in_review` (`ship` swaps the first for the second, and the work is still that session's until its PR lands), other than this run's own spec issue. With both labels unset, nothing reads as claimed. |

`introduced` is never routed `issue`: a Critical/Important one is fixed on the branch or the run
stops; a Minor one is swept or noted.

**Nothing to fix** — nothing routed `must-fix` or `sweep`, and no `ask` item approved — means
review #1 was the last review. Go to Phase 3.

### The batched question

If any item is routed `ask`, ask your human partner **once**, every `ask` item in one message: the
finding, the two options, and the reviewer's recommendation. Wait. Each answer is an option — the
item is swept with that decision, recorded as an `Owner:` line — or "not now", which makes the
item `issue` (or `note`, when it is `introduced`). This is the one planned interrupt in a run.

### The amendment

If anything is routed `sweep` or approved from `ask`, amend the spec before planning the sweep.
Find the spec comment by its `# Spec:` first line — never by position, never `--edit-last` — and
PATCH it in place, appending:

```markdown
## Amendment <K> (v<N>)
_Written by the WDD controller during the run; not reviewed before it ran._
- <what changes> — <why> — origin: <introduced|pre-existing> — <closes #N | part of #N | no issue> — decided by: <controller ruling | owner, YYYY-MM-DD>
```

and bump `**Version:**` to `<N>`. Leave `**Status:**` alone: approval still names the version it
was given at. `<K>` is one more than the amendments already on the spec, from any run. On a
**solution-ready** issue the body is the spec and the gate reads it, so it is never edited: post
the amendment as its own comment headed `# Spec amendment <K>: <issue title>`, with no version,
and write the ledger line without `(v<N>)`.

`must-fix` items are not listed — fixing a defect the branch caused does not change what the spec
asked for. Each amendment line is also a `Ruling:` line, so it reaches your human partner in
Phase 3. Append `Amendment <K>: written (v<N>)` once the PATCH succeeds.

### The sweep waves

Extend the **same plan file**: append a `## Sweep` section holding the sweep tasks, numbered on
from the last spec task, and add their waves to the existing `## Waves` table, numbered on from
the last spec wave. Each task has `Files:`, `Depends:` and `Model:` as usual, and **its Step 1 is
the item's reproduction or failing check, written as the failing test** — a sweep item never ships
without the test that would have caught it. `must-fix` items are planned the same way. Items that
share a file may share a task, if it stays right-sized.

Then the usual self-review, one plan review, and `scripts/plan-tasks --validate`, whose Rule 8
refuses a second `## Sweep` section and a sweep task in any wave at or before the last spec wave.
Append `Sweep plan: waves <A>-<B>`, and run those waves exactly as Phase 2 runs any wave. Write a
`Fixed:` line as each `sweep` or approved `ask` item's task completes.

**One pass.** Nothing found during or after the sweep starts another:

| During a sweep task | Goes to |
|---|---|
| `introduced`, Critical/Important | the task's fix loop |
| `introduced`, Minor | `Sweep: introduced (late)` → review #2 routes it `note`, or `must-fix` if it re-grades it |
| `pre-existing` | `Sweep: pre-existing (late)` → review #2 routes it `issue` or `note` only |

**A sweep task that cannot finish** — its 5-round cap with findings open, or an item that outgrows
admission (undeclared paths, or a design fork after all) — **is never merged**, so nothing it
introduced lands. A `sweep` or approved `ask` item is dropped, not grown: re-routed `issue` (a
`Seen:` line if it carried `#N`), its amendment line struck through with the reason, the version
bumped, and a `Ruling:` line recording it. A `must-fix` item cannot be dropped: it becomes
`Sweep: introduced must-fix (late)`, and review #2's fix dispatch takes it. **No wave runs after
the sweep range** — `wdd` refuses a wave dispatched after `<B>`, and a second `Sweep plan:` line.

**Either way, its branch leaves the task namespace.** `wdd` refuses every `<slug>-t<N>` branch that
is not merged into the plan branch, and merging this one to quiet it would land exactly the work
this paragraph keeps out. Remove the task's worktree with `scripts/wave-worktree remove <slug> <N>`
(which keeps the branch), rename the branch with
`git branch -m <slug>-t<N> <slug>-dropped-t<N>`, push it with
`git push origin <slug>-dropped-t<N>` — task branches are otherwise local, and a link to a ref in
this clone resolves for nobody else — and record
`Ruling: <item> dropped, branch <slug>-dropped-t<N> at <sha7> — <why it could not finish> — <what it costs if wrong>`.
`cleanup` does not delete it: it is the only copy of the dropped attempt, and the `issue` a `sweep`
or `ask` item is re-routed to names the branch and its tip sha, so the attempt can be picked up.

### Reviews #2 and #3

Each is `final-reviewer-prompt.md` on the most capable model, given:

- the **delta** to review: `scripts/review-package PLAN_FILE <Since> HEAD`, where `<Since>` is the
  previous review's `Reviewed:` sha;
- a **fresh** whole-branch package, `scripts/review-package PLAN_FILE "$MERGE_BASE" HEAD`, as
  context — the reviewer must check every caller and user, anywhere on the branch, of what the
  delta changed;
- the previous review's `must-fix` items as `[PRIOR_FINDINGS]`, each to be verdicted ADDRESSED or
  NOT ADDRESSED;
- as `[SWEEP_LINES]`, only the `(late)` `Sweep:` lines written since the previous full-branch
  review — everything older was routed by a review already;
- `open-issues.json` rewritten first, and `[OPEN_ISSUES_FILE]`, `[CLAIM_LABELS]` and `[SPEC_ISSUE]`
  as for review #1; `[REPORT_FILE]` is `<workspace>/final-review-<k>.md` — after an
  owner-directed chain restart, the file name set out in The verdict chain, below.

Their routes are `must-fix`, `issue` and `note` only. If review #2 routes anything `must-fix`:
**one fix dispatch** carrying the complete list — never one fixer per finding; per-finding fixers
each rebuild context and re-run suites, and a real session's final-review fix wave cost more than
all its tasks combined — then **one scoped re-review** of the fix diff (`re-review-prompt.md`),
then review #3, whatever the re-review said. The re-review's out-of-scope observations become
`(late)` `Sweep:` lines `from final review #2 re-review`, and review #3 routes them.

**Review #3, or any later review, with a `must-fix` open** → `Final review #<k>: BLOCKED at
<sha7>`; stop and ask, handing over the open findings, every review report, and the fix history.
Your human partner either directs another fix — one fix dispatch, one scoped re-review, then
review `#<k+1>`, the same shape — or rules a finding acceptable, which reaches that next review in
`[PRIOR_FINDINGS]`. Either way, **merge still needs a `CLEAN` full-branch review of the final
commit. There is no override flag.**

### The verdict chain

Each full-branch review writes `<workspace>/final-review-<k>.md`, opening with
`Reviewed: <sha>`, `Since: <sha>` and `Verdict: CLEAN | MUST-FIX`. `wdd check` and `ship` refuse
unless the chain covers `HEAD`, and `merge` unless it covers the PR head:

1. `final-review-1.md` … `final-review-<n>.md` exist with no gap, and every header parses — a
   malformed header is an absent verdict, never a permitted one;
2. review `<n>` is `CLEAN`;
3. review `<n>` reviewed `HEAD` — for `merge`, the PR head it is about to land;
4. from review #2 on, each `Since:` is the previous review's `Reviewed:`, and an ancestor of its
   own;
5. review #1's `Since:` is an ancestor of `origin/<default-branch>` and of its own `Reviewed:`.

So **anything that changes the code after the last review needs another review before it can
ship**, and every commit on the branch sits inside some review's range — evidence pinned to a
commit, for the same reason as the lane receipt.

**A branch with a full-branch review is never rebased.** Bring it up to date by merging the default
branch in — GitHub's default "Update branch", or `gh pr update-branch` without `--rebase`. The old
head stays an ancestor of the new one, so rule 4 holds and the chain simply extends: the new head
gets the next full-branch review under the rules above, numbered on from the last, and it counts
against the three-review budget like any other. A rebase rewrites every reviewed sha, so no later
review can link to the chain. **If one happens anyway, stop and ask** your human partner; never
restart the chain on your own.

On an owner-directed restart, rename the existing verdict files to
`final-review-<k>.superseded-<sha7>.md` (`<sha7>` being each file's old `Reviewed:` sha), so the
chain, which reads only names that are exactly `final-review-<k>.md`, no longer sees them while
their history stays on disk. The restarted chain's **file** names begin again at
`final-review-1.md` — the chain rules need that, and its first review starts from a fresh
`$MERGE_BASE` — passed to the reviewer explicitly as `[REPORT_FILE]`. Its **ledger** lines and the
reviewer's `[REVIEW_NUMBER]` continue the run's count: `Final review #4:` resolves
`Final review #3: BLOCKED`, routing from #2 on stays `must-fix`, `issue` and `note`, and the budget
is your human partner's call, because they directed the restart.

The whole-branch review is load-bearing, not a formality. **This is the second
of the two review stages, and the one that catches what the first cannot:** on
a real run it caught a hole spanning two modules that all four per-task reviews
had passed, each task's diff being correct in isolation and their combination
not.

## Phase 3 — Ship and clean up

Input: a plan branch whose every wave integrated and whose last full-branch
review is `CLEAN` on `HEAD`. Output: a merged PR and nothing left behind.

**Before anything is deleted, hand over the rulings.** Collect every ledger
line containing `Ruling:` — pre-flight rulings, parked findings, breaker
adjudications, all of them — into your final message under "Rulings I made", in
the order you made them, each with what it costs if wrong. The list is
exhaustive: if the ledger holds a ruling, the list holds it. That list is the
only place the decisions you took on your human partner's behalf reach them —
they read it and rework whatever you got wrong. `cleanup` deletes the
workspace, so **a ruling that dies with the workspace was a decision made in
secret.**

**Then file what the sweep did not take, before `ship`.** Each `issue` item with no duplicate is
filed with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/file.py "<title>" --body-file <path>` — the
finding, its evidence pinned to a commit, its reproduction if any, **which sweep condition it
failed**, and `found during #<spec issue>, review #<k>` — then a `Filed:` line. If `file` refuses
for want of `backlog_milestone`, use `gh issue create` with no milestone, and say so. A matched
item gets a `Seen:` line and no new issue; a `note` item, a `Note:` line. No comments are posted on
issues: `ship` writes a `## Sweep` section into the PR body from these lines, and GitHub puts each
mention on the issue's timeline. After the rulings, your final message lists the issues the sweep
closed, filed and noted. A `BLOCKED` run files nothing.

Run `scripts/wdd`, from the **plan worktree**:

```bash
scripts/wdd check   PLAN_FILE   # verify only, no side effects
scripts/wdd ship    PLAN_FILE   # check, push, open PR, sweep section, in_review label

# bringup + validate -> a receipt for this exact commit
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lane.py run --session <session-name>

scripts/wdd merge   PLAN_FILE   # receipt + green -> merge, clear claim label, sync main
scripts/wdd cleanup PLAN_FILE   # task branches, worktrees, workspace
```

**The lane step is yours to run, not the human's** (Automation boundary). Put
it last before `merge`. The order is not arbitrary and it is not about `ship`:
the receipt is pinned to a **commit**, so what invalidates it is any push
between the run and the merge — running the lane immediately before `merge`
is simply the ordering with the smallest window for one. Run it from the plan
worktree, and pass `--worktree` explicitly when standing in a submodule's
worktree, where the caller's checkout is ambiguous. It takes a machine-wide
mutex and may wait behind another session; that is a queue, not a question.
**Its exit status is not the verdict** — the receipt's existence is (see
below), and `lane run` has been observed exiting 0 on a failed `validate`
(#25).

`lane.py` is spelled from `${CLAUDE_PLUGIN_ROOT}` while `wdd` is bare because
they live in different places: `wdd` is this skill's own script, and the lane
belongs to the plugin, which drives projects this skill is not the only entry
point to.

`check` reads the ledger and refuses on any of: a `Wave N: dispatched` with no
matching `integrated`; an unresolved stop marker (Ledger, above); a second
`Sweep plan:` line, one that does not parse, or a wave dispatched after its
range; a ledger whose first
line names a different plan. `check` and `ship` also refuse unless the verdict
chain covers `HEAD`, and `merge` unless it covers the PR head (Final Review,
above). It then verifies, against git rather than against the ledger,
that **every task branch is an ancestor of the plan branch** — an integrator
that died after writing its line leaves the claim without the merge, and
nothing else in the workflow would notice.

`check`, `ship`, `merge` and `cleanup` all take the base branch from the
project config's `default_branch` — every one but `cleanup` needs it for the
verdict chain's rule 5 — the same source Phase 2 step 1 uses, and never
`origin/HEAD`, which is a clone-time cache that survives a remote renaming its
default branch and is absent entirely from an `init`+`fetch` checkout.
`cleanup` gates branch deletion on that ref, so a stale name that is still a
live branch would decide the fate of the only copy of a task's work. All four
refuse rather than guess.

`ship` pushes, opens the PR, appends its closing keyword and the `## Sweep`
section, swaps the issue's `status_labels.ongoing` for `status_labels.in_review`,
and stops there. The issue is the one the branch names, and only once the PR
body closes it. `merge` takes both labels off that issue once the PR has
landed, and never touches `status_labels.blocked`, which is someone else's to
retract. Every label step is soft and spoken: a missing number, an unset label
or a refusing `gh` is reported and never blocks a ship or a merge.

`merge` is what lands the PR, and what it replaces is a human remembering that
the branch was validated. That bound holds until the first time it does not,
and nothing observes the failure — so `merge` is gated on **evidence** instead.

**The evidence is a lane receipt.** `lane run` writes
`.pitcall/receipts/<sha>.json` when — and only when — `validate` passed against
that exact commit, in a tree with no uncommitted changes to tracked files, with
HEAD unmoved for the duration. **Its existence at that path is the verdict**;
the body is read only to tell you when and by whom, and a receipt that will not
parse is corrupt, which means *absent*, never *permitted*. The body is checked
for one thing beyond the message — that it names the commit it is filed under,
which catches a receipt copied or symlinked onto another commit's name. That
check is **one-directional**: it can turn a pass into a refusal, never a
refusal into a pass, so existence remains the only thing that permits
anything.

Pinning to the sha rather than to the branch is the whole point. A receipt for
`branch@<a>` says nothing about `branch@<b>`, so **a review fix pushed after the
lane ran cannot inherit the approval of the commit it replaced** — the likely
failure, not an exotic one. The consequence for you: run the lane, then merge,
and any push in between means running the lane again.

`merge`, in order:

1. resolves the PR for the branch and takes **the head sha GitHub reports now**
   — not this checkout's HEAD, not a sha resolved earlier in the run. It
   refuses if the PR targets a base other than the config's `default_branch`
   (retargeting happens on GitHub, where nothing here can see it);
2. checks the verdict chain (Final Review, The verdict chain) against **that
   head sha**, and refuses a commit no clean full-branch review covered — the
   commit that lands is the one the last review must have seen;
3. refuses if this checkout's `HEAD` is not the commit the PR would merge —
   everything `check` verified was verified against local `HEAD`, so a merge
   landing something else means that verification was about the wrong commit,
   and reporting *merged* would describe work this run never examined;
4. checks the receipt for that sha **before waiting on anything**. Refusing
   early costs nothing; refusing after the wait means having spent ten minutes
   on checks for a branch that could never have merged;
5. waits for the config's `required_check` to go green, **bounded** — 30
   minutes, polled every 20s, overridable in seconds with `WDD_CHECK_TIMEOUT`
   and `WDD_CHECK_POLL`. A bound that expires reports *not merged, still
   running* and exits 3, having changed nothing. Anything that is not an
   outright success — failed, skipped, cancelled, or a check that never appears
   — is not a pass. Where the name appears **more than once** in the rollup, an
   external app's commit status colliding with a workflow job's name or two
   workflow files each defining one, **every match is judged and the worst
   wins**: a green entry listed first cannot carry a red or still-running one;
6. merges with `gh pr merge --merge --match-head-commit <sha>`,
   **server-side**. A local `git merge` runs zero checks and moves HEAD in a
   checkout other sessions are reading. Not a squash: that mints a new commit,
   so what lands is a sha nothing validated, and it also makes `cleanup`'s
   ancestry test permanently false. **The head pin is the other half of the
   receipt** — see below;
7. clears the claim label: removes `status_labels.ongoing` and
   `status_labels.in_review` from the issue the branch names, when the merged
   PR's body closes it. It runs before the sync below, whose exit 4 is about
   the main checkout and not the merge, and it is soft: a refusal is reported
   and never changes the exit status;
8. fast-forwards the main checkout, then runs the project's optional
   `refresh_commands` there **best-effort** — the merge has already happened
   and a regeneration failing must not unwind it.

**The head sha is read once, and the wait can run for half an hour.** Pushing a
review fix while checks run is the ordinary way of working, not an exotic
sequence — and `statusCheckRollup` reports checks for the *current* head, so a
push mid-wait produces a green signal about a commit no receipt covers. Every
signal agrees that merging is fine; only the sha dissents. `--match-head-commit`
closes that **server-side**: GitHub refuses if the head has moved. Re-reading
the head just before merging would only narrow the window, and a narrower race
is the kind nobody reproduces afterwards. A `gh` too old for the flag (it
arrived in 2.96) makes `merge` refuse rather than merge unguarded — a guard
that is silently absent looks exactly like a guard that passed.

**A PR that is `BEHIND` its base, or that has conflicts (`DIRTY`), is
refused** — checked before the wait and again on every poll. Merging a `BEHIND`
one produces a merge commit combining two states nothing validated, the
migration-number hazard described below; a `DIRTY` one GitHub will not merge at
all, so waiting on it only spends the bound to arrive at the same answer.
`DIRTY` is the commoner of the two — measured 16 to 4 across 20 open PRs.
The remedy for either is to **merge the default branch in, never rebase** (Final
Review, The verdict chain), resolving any conflict in that merge. That changes
the head sha, so the new head needs a full-branch review, then the lane on that
commit, then `merge` again: neither the review nor the receipt carries across.

**That guard is best-effort, and the reason is GitHub's rather than this
script's.** `mergeStateStatus` is computed lazily, and the first view of a PR is
the request that *triggers* the computation: measured, one batch over 20 open
PRs answered `UNKNOWN` for all 20, while the identical query moments later
returned real values. `merge` therefore re-reads once, after a short pause, when
the first answer is `UNKNOWN` — but **a PR whose checks are already green can
still be merged with neither read having seen a computed value**, because the
poll loop may run only once and see `UNKNOWN` too. Branch protection is what
makes this airtight. This step narrows the window; it does not replace it, and
nothing here can observe whether a project has that protection enabled.

**A rejected push is not a force-push cue.** It means the remote moved:
investigate what landed there. Force-pushing a shared branch destroys whatever
that was, and it is never this skill's call to make on its own.

The fast-forward is the one place any of this touches the shared checkout, and
it is safe by construction rather than by care: `git merge --ff-only` cannot
create a commit — it advances a pointer or it refuses. **A refusal means the
checkout has diverged**, which is the condition that should be impossible in a
tree nobody authors in, so `merge` names the local commits and stops (exit 4).
It does not repair: a merge there would mint one more commit and erase the
evidence of the thing that was supposed to be impossible.

Branch protection stays upstream of all of this and is never routed around. A
green check alone was never enough — CI proved the branch clean against the
base *as of when it ran*, and two branches can each add a differently-named
migration claiming the same number, which git merges cleanly and leaves broken.
Requiring the branch to be **up to date with the base** is what closes that, and
it also keeps the receipt honest: updating a stale branch — by merging the base
in, never by rebasing — changes its head sha, which invalidates the receipt and
needs a full-branch review of the new head.

`cleanup` runs only once the plan branch is an ancestor of the default branch
on `origin`; before that, deleting a task branch would destroy the only copy of
its work. It removes the task branches, their worktrees, and this plan's
workspace — sibling workspace directories belong to other plans and are left
alone. Its `git worktree remove --force` is safe for that same reason and only
for that reason: past the ancestor gate every task branch's commits are already
on `origin`, so the only thing `--force` can still discard is uncommitted
scratch in a task worktree — work no review ever saw and no merge ever took.

### Why the ship step is a script, not a menu

The obvious alternative is a skill that verifies the suite, presents the human
a menu — merge locally, open a PR, keep the branch — and executes the choice.
Two things make that the wrong shape here.

**Its local-merge option runs `git checkout <base>` and `git pull` in the main
checkout**, which concurrent sessions are using. A real run measured the base
branch moving twice in the minutes between such a menu being presented and the
controller acting, with another session's uncommitted edits in the tree
throughout.

**Its PR option is safe, and this skill used to say "take the PR option" — but
that is a request, and requests lose.** Measured on the project this skill grew
in, three separate written rules were argued past, silently skipped, or left
unrun within one week. Each was fixed only by making the wrong thing
mechanically unreachable. `wdd` refuses to run outside a plan worktree, so the
shared checkout is never in reach — with one deliberate exception, the
`--ff-only` sync at the end of `merge`, which cannot create a commit and
reports rather than repairs when it will not apply.

A menu also never covered cleanup. This document once said task branches
"persist until the final PR"; nothing then deleted them. Measured on one
repository: 33 task branches on disk, 20 of them already merged.

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Each task's migration file has its own name, that's disjoint enough" | A unique filename can still carry a duplicate numeric prefix (`0042_a.sql`, `0042_b.sql`) — file-disjointness checking never notices, because the paths really are disjoint. Assign the migration number itself as a global singleton at planning time, the same as a submodule bump slot. |
| "This task's boundary is obvious, it doesn't need its own stop-and-report instruction" | Stated for one lane and assumed for the rest is how it goes missing everywhere else. NEEDS_CONTEXT-on-out-of-lane is a default every task brief carries, not an instinct to rediscover per task. |
| "These two tasks are unrelated, they can share a wave" | Unrelated is not the test. Disjoint declared file sets and no dependency between them is the test, and `--validate` decides it. |
| "The validator is being pedantic, I'll run anyway" | The validator is the only thing making the invariant a guarantee rather than a hope. A plan that fails it will conflict at a merge, hours later, with worktrees already created. |
| "I'll split this file between two tasks by region" | Not permitted. It looks disjoint and is not — the merge is textual, not semantic. Put them in different waves. |
| "The plan says 264 tests, this task shows 261 — it's broken" | Absolute totals are baseline-plus-serial-delta. The integrator verifies totals; a task does not. |
| "This merge conflicted, I'll just resolve it" | A conflict means the plan violated its own guarantee. Fix the plan, not the merge. |
| "I need one file outside my list, it's a one-line change" | Report NEEDS_CONTEXT. You cannot see what your siblings are editing right now. |
| "One task is stuck, I'll hold the wave" | Green siblings merge. The stuck task retries in the next wave. |
| "Each task got a review, the branch review is redundant" | Four per-task reviews passed a sibling-module hole the whole-branch review caught. |
| "The file was already messy, cleaning it isn't my task" | It is, for the blocks you are editing. A file that gets worse every time it is touched is the failure the boy-scout rule exists to stop. Bound: blocks you edit, never the whole file. |
| "It's a one-line rename, and my file set covers it" | Your file set covers what you may *touch*, not what others may *see*. A sibling importing that symbol merges cleanly and breaks. Would a file outside your set have to change? Then it is the planner's call. |
| "The test was clearly obsolete, I deleted it" | Needs a citation to the plan or spec text that changed the behaviour — without one it's a failing test you removed, not a stale one. |
| "RED failed, that's good enough" | RED failing with ImportError proves the module is missing, not that the assertion discriminates. A test that cannot fail is worse than no test, and this is exactly how one ships. |
| "Close enough on spec compliance" | The reviewer found spec gaps, so it is not done. Fix, or hit the cap and adjudicate — those are the only two exits. |
| "I'll just review the diff myself instead of dispatching a reviewer" | You are the coordinator. Reviewing inline burns the context window you need to keep driving the work; dispatched, the diff and the evaluation live in the reviewer's context and only the findings come back. |
| "I'll fix it myself, dispatching is overhead" | Controller fixes pollute your context and skip review entirely. Resume the implementer. |
| "One more round will converge" | Past the cap, rounds do not converge — the failure is structural. Adjudicate and route. |
| "The reviewer will just find something new anyway" | Scoped re-reviews verify fixes; they cannot wander. New findings on untouched code go to the ledger, not the loop. |
| "This finding is obviously wrong, I'll drop it" | You adjudicate only at the cap, and every ruling is a ledger entry. Silent discards are forbidden. |
| "The fix was small, skip the re-review" | Unreviewed fixes are how regressions land. Every round ends with a scoped re-review. |
| "Reviews slow the loop down" | The loop without reviews is unverified churn. Reviews are its brakes and its steering. |
| "Ledger bookkeeping is overhead" | The ledger is what survives compaction. Controllers without one have re-dispatched entire completed task sequences. |
| "The implementer spawned its own reviewer — free extra assurance" | It is a duplicate seat reviewing the same diff; the task review is the gate. A worker-spawned reviewer is a defect to flag, not rigor. |
| "The push was rejected — a force-push will fix it" | A rejected push means the remote moved. Investigate what landed; force-pushing a shared branch is never this skill's own call. |
| "It's not part of the spec — I'll file a follow-up" | That sentence is how the backlog grew faster than it shrank. Tag it, put it on the sweep list, and let review #1 route it: a small one is fixed in this run. |
| "It's only a Minor this branch introduced — I'll file it" | An `introduced` finding never leaves as an issue. It is fixed, or noted in the PR. |
| "The sweep turned up more — one more sweep will finish it" | One pass. A second is how the growth moves from the backlog into the branch; what the sweep finds goes to `issue` or `note`. |
| "The review was clean an hour ago, and this fix is tiny" | `wdd` refuses a commit no clean full-branch review covers. Review it, or do not ship it. |
