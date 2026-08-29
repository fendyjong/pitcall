---
name: wave-driven-development
description: Use when taking an approved spec through implementation to a mergeable branch, in the current session
---

# Wave-Driven Development

Take an approved spec to a green branch: plan the work into waves that cannot
conflict, run each wave's tasks concurrently in isolated worktrees, merge at
every wave boundary, and finish with one whole-branch review and one PR.

**Core principle:** conflicts are prevented at planning time, not resolved at
merge time. Within a wave, tasks have disjoint file sets and none depends on a
sibling — so merges are clean by construction and merge order does not matter.

**Three phases.** Phase 1 plans and validates. Phase 2 executes. Phase 3
ships and cleans up. Do not begin Phase 2 with a plan that has not passed
validation, and do not begin Phase 3 with a wave that has not integrated.

**Relationship to pitcall:subagent-driven-development:** same quality gates,
different scheduling. Every SDD rule about reviews, the fix loop, the ledger,
model selection, and the controller never editing code applies unchanged.

**Narration:** between tool calls, narrate at most one short line — the ledger
and the tool results carry the record.

## When to Use

- **Have an approved spec, nothing planned yet** → start at Phase 1 — Plan.
- **Already have a wave plan that has passed `--validate`** → skip straight
  to Phase 2 — Execute.
- **Every wave in the plan turns out width 1** → say so, stop, and fall back
  to `pitcall:subagent-driven-development` instead. A strictly serial
  plan has no concurrency to buy, and worktrees, wave merges, and integrator
  dispatches are pure overhead on top of one.

## Phase 1 — Plan

Input: an approved spec. Output: a wave plan that has passed `--validate`.

1. **Invoke `pitcall:writing-plans`** for the task content — file
   structure, steps, code, tests. That skill stays upstream and unforked.
   Handle two things in its output rather than inheriting them as written:
   - It ends by asking the human to choose an executor. Answer it yourself —
     the answer is always this skill, `wave-driven-development`.
   - Its mandated plan header names `pitcall:subagent-driven-development`
     as the required sub-skill. Rewrite that to name `wave-driven-development`.
   Save the plan where `pitcall:writing-plans` saves it:
   `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`.

2. **Compute the file-overlap constraints:** `scripts/plan-tasks PLAN_FILE`
   (no `--validate`) prints every task's declared paths and, at the end,
   `# File overlaps - these pairs MUST NOT share a wave` — the exact task
   pairs that can never share a wave. This is what you assign waves from in
   the next step, not a re-read of the plan prose: the tool exists so path
   overlap is computed once, not judged by eye per task.

3. **Draw task boundaries with the invariant in mind**, applying the four
   heuristics below against the overlap list from step 2 — this decides
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

4. **Assign global singletons at planning time, before any task is
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
   straight into the plan's `**Files:**` block. Never leave a placeholder like
   `00xx_add_positions.sql` — Rule 5 only catches two tasks claiming the
   *same* number, and has nothing to say about a number nobody assigned. A
   placeholder validates clean and is a planning defect the tool structurally
   cannot see.

5. **Emit the `## Waves` table**, and in every task's `**Interfaces:**`
   block a `Depends:` line naming task numbers or `none` — the leading `- `
   is required; it is what the validator's parser matches on:

   ```markdown
   ## Waves

   | Wave | Tasks | Rationale |
   |---|---|---|
   | 1 | 1, 4 | disjoint files; neither depends on anything |
   | 2 | 2, 3, 5 | all depend on 1; disjoint from each other |
   ```

   ```markdown
   **Interfaces:**
   - Depends: 1, 3
   - Model: standard
   - Consumes: `build_wiring` from Task 1 — its test proves the registration was removed cleanly.
   ```

   `Model:` is one of `cheapest` / `standard` / `most-capable`, chosen per
   **Model Selection** below and validated by Rule 7. **Assign it here, in
   Phase 1 — not at dispatch.** This is the same reasoning as migration
   numbers in step 4: a decision deferred to dispatch is a decision skipped.
   A wave is dispatched in a single message, so a controller choosing at
   dispatch time is judging every task's complexity at once while composing
   every call; and the Agent tool's `model` is *optional*, so the omission
   costs nothing, says nothing, and silently inherits the session's own —
   usually most expensive — tier. Nothing downstream records which model ran,
   so the mistake leaves no trace to find later. Phase 1 already knows each
   task's file count and character, which is exactly what the tier depends on.

   `**Files:**` gains a `Delete:` category alongside `Create:` / `Modify:` /
   `Bump:` / `Test:` — declare migration files and submodule paths like any
   other path:

   ```markdown
   **Files:**
   - Create: `db/migrations/0042_add_positions.sql`
   - Modify: `services/billing/app/checkout.py`
   - Delete: `services/search/app/legacy_index.py`
   - Bump: `libs/shared`
   - Test: `services/billing/tests/test_checkout.py`
   ```

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
the covering test file (step 3, above) is what lets tests move with the code;
other files still reference the symbol and are not in the set.

7. **Validate:** `scripts/plan-tasks --validate PLAN_FILE` (script paths
   here and in Phase 2 are relative to this skill's own directory, wherever
   the plugin is installed). Fix the plan and re-run until
   it exits 0 — the printed problems name the exact rule and tasks involved.
   A plan that fails validation is not executable; do not proceed to Phase 2
   with it.

## Phase 2 — Execute

Input: a plan that has passed `--validate`. Output: every wave merged into
the plan branch, ready for Final Review.

**Setup, once:**

1. Create or verify the plan worktree via `pitcall:using-git-worktrees`,
   **but base it on `origin/<default-branch>` explicitly**, after a
   `git fetch` — `<default-branch>` being the project config's
   `default_branch`, never a name assumed here:

   ```bash
   git fetch --no-tags origin <default-branch>
   git worktree add <path> -b <plan-branch> origin/<default-branch>
   ```

   That skill's own command is `git worktree add "$path" -b "$BRANCH_NAME"`
   with **no base ref**, so the branch starts at whatever HEAD the main
   checkout happens to be on. The main checkout is shared by concurrent
   sessions: its local default branch is routinely ahead of the remote one by
   commits another session has not pushed — in a repository whose git hooks
   commit regenerated output, every merge there produces such a commit — and
   it may not be on the default branch at all. Inheriting that silently forks
   your work off someone else's unpushed WIP — and pushing then publishes
   their work-in-progress under your branch, which is a real, observed
   failure, not a hypothetical one.

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
4. Check for an existing ledger (`<workspace>/progress.md`). If one exists,
   this run is a resume — follow Resuming an interrupted run, below, in
   full before dispatching anything.
5. Re-run `scripts/plan-tasks --validate PLAN_FILE`. A plan can be
   hand-edited between Phase 1 and Phase 2; re-validate before any worktree
   is created.

**Per wave, until every task is complete or escalated:** everything below
runs from the plan worktree unless a command names a different directory
explicitly — task worktrees are always spelled out as
`.worktrees/<slug>-t<N>`. Every subagent dispatch also names its
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
2. **Restore immediately — a task worktree is born dirty.** A repository
   that regenerates tracked output from a git hook rewrites those paths in
   every new worktree: `.git/hooks/post-checkout` has no branch gate and
   fires on `git worktree add` itself, before an implementer runs a single
   command — unlike `post-commit`, which such repositories usually gate to
   the default branch so it never fires on a task branch. Right after step 1
   returns, restore every path the project declares regenerable:

   ```bash
   scripts/project-config regenerated_paths |
     xargs -r git -C .worktrees/<slug>-t<N> checkout --
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

   `git worktree prune && git worktree add .worktrees/<slug>-t<N>
   <slug>-t<N>` (no `-b` — checks out the branch that already exists rather
   than creating a new one). Then do the two setup steps `wave-worktree
   create` would otherwise have done, in order: `git -C
   .worktrees/<slug>-t<N> submodule update --init --recursive`
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
   `model` parameter** — the plan already decided this (Phase 1, step 5); here
   you only resolve tier to the concrete model available in this session. The
   parameter is optional and an omission is silent, so this is the one step in
   the wave where doing nothing produces a plausible-looking, wrong result.
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
5. As each task finishes, read its reported status before anything else.
   `DONE_WITH_CONCERNS` is neither `DONE` nor `BLOCKED`: read the concerns
   first. If they bear on correctness or scope, resolve them before review
   — ask the implementer, or amend the plan and re-dispatch, same as any
   other open question. If they are pure observations, note them in the
   ledger and proceed to review as normal. A report with unread concerns
   must never reach the reviewer with its status treated as a plain `DONE`.

   Then review it: `scripts/review-package PLAN_FILE
   "$BASE" <slug>-t<N>` plus `task-reviewer-prompt.md`. For a per-task
   review, the third argument is always the task's branch name, never the
   literal word `HEAD` — run from the plan worktree, `HEAD` there names the
   plan-branch tip, which
   during a single wave equals `$BASE` (nothing has merged into the plan
   branch yet), so a literal `HEAD` silently produces an empty package:
   exit 0, "0 commit(s)," no warning, and the reviewer then reviews nothing.
   Then run the fix loop **inside that task's own worktree**: capture the
   branch's tip (`git rev-parse <slug>-t<N>`) right before dispatching each
   review or re-review — that snapshot is `FIX_BASE_SHA` for the following
   round's re-review. The implementer resumes, and each round's fix diff
   gets a scoped re-review via `scripts/review-package PLAN_FILE
   "$FIX_BASE_SHA" <slug>-t<N>` (third argument is still the branch name,
   never literal `HEAD`) plus `re-review-prompt.md`. That is why the
   worktree is not removed until after the merge, not just until the
   implementer's first report.
6. Once every task in the wave is green, dispatch `integrator-prompt.md` to
   merge the wave's branches into the plan branch and run the verification
   the merged tasks name. Once it reports INTEGRATED, write `Wave N:
   integrated (...)` to the ledger — this is the line Resuming an
   interrupted run (step 1, below) looks for to know the wave closed cleanly
   rather than stalled mid-run.
7. Before removing anything, repeat step 2's restore in each merged task's
   worktree, in case a later hook run re-dirtied it since creation.
8. `wave-worktree remove <slug> <N>` for each merged task — this deletes
   the worktree only. The branch `<slug>-t<N>` is kept and stays on disk
   through every later wave, until the final PR is opened. The plan branch
   has now advanced by this wave.

## Model Selection

Inherited from `pitcall:subagent-driven-development`, restated here so a template's
"choose per SKILL.md Model Selection" resolves in one hop instead of leaving this document.

**One thing differs from SDD, and it is the point of this section:** for **implementers**, the tier
is not chosen here at all — it is read from the task's `Model:` line, assigned in Phase 1 and
enforced by `--validate` Rule 7. SDD can afford to choose at dispatch because it dispatches one
implementer at a time; WDD dispatches a whole wave in one message, where choosing means judging
every task at once and the cheapest move is to omit the parameter entirely. **Dispatch transcribes
the plan's tier; it does not re-derive it.** If the tier looks wrong while dispatching, fix the
plan and re-validate — do not silently substitute, or the ledger will record a tier the plan
does not contain.

The tiers below are what a planner picks *from*, and what the roles with no plan line — reviewer,
integrator, final review, fix-loop escalation — are chosen by at dispatch. Use the least powerful
model that can handle the role:

- **Implementer, mechanical task** (isolated functions, a clear spec, 1–2 files, or the plan
  supplies the exact code to transcribe): cheapest tier.
- **Implementer, integration or judgment task** (multi-file coordination, pattern matching,
  debugging): standard tier.
- **Implementer, architecture or design task**: most capable available model.
- **Task reviewer and re-reviewer**: scale to the diff's size, complexity, and risk — a small
  mechanical diff does not need the most capable model, a subtle concurrency change does. Scoped
  re-reviews of small fix diffs take a cheap-to-mid tier.
- **Integrator**: standard tier, always — the role merges and verifies, it never authors code.
- **Final whole-branch review**: the most capable available model, not the session default (see
  Final Review, below).
- **Fix-loop escalation (rounds 4–5):** at least one tier above the implementer that got stuck.

**Always name the model explicitly at dispatch.** An omitted model inherits the session's own model
— often the most capable and most expensive — which silently defeats every rule above. Turn count
beats token price: a cheap model that takes three attempts on an ambiguous task costs more than a
standard one that takes one.

## Ledger

`<plan-worktree>/.superpowers/wdd/<plan-slug>/progress.md`, first line naming the plan file.

The `Task <N>: complete` line keeps SDD's exact wording so its compaction-recovery property
survives: after a context loss, a task carrying that line is done and must not be re-dispatched.
Wave frames wrap it. A fix round's line is one normative format: `Task <N>: fix round <R>/5 (<A>
addressed, <O> open — <finding text>)`, the `— <finding text>` clause present whenever `<O>` is
nonzero and omitted when it is zero. This is the only fix-round format — do not also encode a
commit range into this line; `git log BASE..<task-branch>` is the source for committed progress.

```
# WDD ledger — plan: docs/superpowers/plans/2026-08-02-example.md
Wave 1: dispatched (BASE a1b2c3d, T1 @ 2026-08-02-example-t1 [cheapest/haiku], T4 @ 2026-08-02-example-t4 [standard/sonnet])
Task 1: complete (commits a1b2c3d..d4e5f6a, review clean, branch 2026-08-02-example-t1)
Task 4: fix round 1/5 (2 addressed, 1 open — retry does not clear the idempotency key on failure)
Task 4: fix round 2/5 (1 addressed, 0 open)
Task 4: complete (commits e5f6a7b..b7c8d9e, review clean, branch 2026-08-02-example-t4)
Wave 1: integrated (merge 9f2c1ab, pytest 264 passed / 22 skipped, worktrees removed)
Wave 2: dispatched (BASE 9f2c1ab, T2 @ 2026-08-02-example-t2 [most-capable/opus], T3 @ 2026-08-02-example-t3 [standard/sonnet], T5 @ 2026-08-02-example-t5 [cheapest/haiku])
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
*worktree's* root, not the main checkout's. A scan of only `<repo-root>/.superpowers/wdd/` from the
main checkout then finds nothing and reports "nothing to resume," the one answer that invites a
fresh start over a run that is in fact resumable. Enumerate roots with `git worktree list
--porcelain` and glob `<root>/.superpowers/wdd/*/progress.md` under each. Ledgers with an open
wave: exactly one, resume it; none, nothing to resume; more than one, stop and ask which, because
guessing picks somebody's other run.

**1. Find the open wave** — a `Wave N: dispatched` with no matching `Wave N: integrated`.

- **Found one** → this wave was interrupted; resume it, starting at step 2 below.
- **None, but the plan's `## Waves` table names a wave after the highest `Wave N: integrated` in
  the ledger** → the crash landed between that wave's integration and the next wave's dispatch, a
  wide window since the controller does teardown and next-wave planning there. Resume at that next
  wave: continue through steps 2–3 below (both are cheap and safe regardless), skip step 4 (nothing
  in this wave has been dispatched yet, so there is no per-task state to recover), then dispatch it
  fresh per Phase 2 step 4. Never re-dispatch a wave that already carries its `integrated` line.
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
which is exactly the `regenerated_paths` list Phase 2 step 2 restores from — read it the same way,
and exclude nothing beyond it. Nothing in a diff distinguishes a hook's output from an
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
first via the retry path in Phase 2 step 3 (reuse the existing branch, never delete it); the `git
worktree prune` immediately before that step's `git worktree add` is not optional here — a worktree
lost to a crash is exactly the "missing but already registered" state `prune` exists to clear.

**This contract is not only for the implementer.** When this task is later reviewed, its
`[GLOBAL_CONSTRAINTS]` (`task-reviewer-prompt.md`) must also carry the sentence "this task was
resumed after an interruption" — the only signal that fires the reviewer's own check 6 (Refactoring
and Test Hygiene) for a silent discard. `task-brief` renders identical text for a fresh and a
resumed dispatch, so nothing else carries this forward.

## Failure policy

| Situation | Action |
|---|---|
| A task hits the 5-round fix cap with load-bearing findings open | Green siblings merge. The task is re-dispatched in the next wave — fresh implementer, one model tier up, carrying the brief, the report file, and the open findings. Its dependents wait one wave. |
| The retry also fails | `Task <N>: BLOCKED` in the ledger, stop, report with the findings, the plan text they collide with, and the fix history. |
| The integrator hits a merge conflict | Hard BLOCKED. Under the core invariant this cannot happen unless the plan violated its own guarantee, so it is a planning defect, not a merge to resolve. Re-validate the plan and surface it. |
| A task's diff touches an undeclared path | Spec failure for that task; it enters the fix loop. If the file is genuinely needed, the plan is wrong — fix the plan and re-validate before continuing. |
| An implementer reports NEEDS_CONTEXT | Answer and re-dispatch. Siblings continue unaffected. |

Minor findings and parked findings behave as in SDD: recorded in the ledger, handed to the final
whole-branch review for triage.

## Inherited from subagent-driven-development

v2 changes *when tasks run*, not *how they are judged*. These carry over verbatim and the plan must
not re-derive them:

- **Fresh subagent per task**, never inheriting the controller's context.
- **Two-stage review**: a per-task spec-compliance + quality gate, then one whole-branch review on
  the most capable model. The second stage is load-bearing — on a real run it caught a hole that
  spanned two modules and that all four per-task reviews had passed, because each task's diff was
  correct in isolation and only their combination was not.
- **Every `⚠️ Cannot verify from diff` item is resolved by the controller before that task
  closes.** `task-reviewer-prompt.md` delegates this explicitly; it is never suppressed and never
  decided by the reviewer itself.
- **Fix loop with a 5-round cap**; rounds 1–3 resume the original implementer, rounds 4–5 escalate
  to a fresh implementer one model tier up.
  If the original implementer is unreachable — always true after a session death, since a subagent's
  context does not survive it — dispatch a fresh implementer carrying the brief path, the report-file
  path, and the open findings. **Name its model explicitly even here:** at rounds 1–3 this
  replacement is forced by unreachability, not by the cap, so it is not an escalation — use the same
  tier the task's implementer dispatch would otherwise use at that round (Model Selection, above),
  and reserve "one tier up" for the real escalation at rounds 4–5. An unnamed model silently inherits
  the session's own, often most expensive, tier. The report file is the persistent memory either way,
  which is what makes the loop survivable rather than merely restartable.
- **Adjudication only at the cap**, and every ruling is a ledger entry. Silent discards are forbidden.
- **Never pre-judge findings for the reviewer** — never instruct a reviewer to ignore or not flag a
  specific issue; let it raise the finding and resolve it in the review loop instead. This is more
  load-bearing here than in SDD: the Automation boundary (below) grants the controller authority to
  resolve a reproduction-backed finding itself, which is a standing temptation to tell the reviewer
  not to raise it and skip the loop entirely. If a dispatch prompt contains "do not flag," "don't
  treat X as a defect," "at most Minor," or "the plan chose" — stop: that is pre-judging.
- **Model selection tiering**, with the model always named explicitly at dispatch. **The tiers
  are inherited; where the implementer's tier is *decided* is not** — SDD chooses at dispatch,
  WDD reads it from the plan's `Model:` line (Model Selection). Reviewer, integrator, final
  review and fix-loop escalation are still chosen at dispatch, exactly as in SDD.
- **The controller never writes code.** Controller edits pollute the coordination context and skip
  review.
- **Artifacts hand over as file paths**, never pasted through the controller's context.

## Automation boundary

The human owns the spec, and the human owns the lane run that validates the branch. From an
approved spec the skill runs unattended through to a **merged** PR — `wdd-finish merge`, whose
bound is a lane receipt for the exact commit rather than a human's attention (Phase 3). What is
automated is *landing already-validated code*; authorising the validation is not. Within that, it
**interrupts only when human input is genuinely needed**.

**Resolve autonomously, record the ruling:**
- A reviewer finding backed by a **working reproduction**, including one that contradicts the plan's
  own text. The plan is a means, not the authority; a reproduction outranks it. Every such ruling is
  a ledger entry and is handed to the final whole-branch review.
- This test **supersedes `task-reviewer-prompt.md`'s own framing for plan-mandated findings**
  ("the human decides"): under v1 that human-decides default fired 4 times in 4 tasks and would
  have deadlocked an unattended run, and all four arrived with working reproductions — exactly the
  case the rule above resolves autonomously instead.
- Anything the fix loop closes within its round cap.

**Interrupt and ask:**
- A finding with **no reproduction** that would require contradicting the plan.
- A genuine design fork — two defensible resolutions with materially different outcomes.
- A BLOCKED the controller cannot resolve, or a validation failure implying the spec is wrong.
- A ruling that would **weaken** an authentication, tenancy, or secrets check, or any action that is
  destructive or irreversible. This is scoped to the controller's own decisions, not to task
  content: in a codebase where nearly every task touches tenancy, "the diff mentions `org_id`" is
  not a trigger. "I am about to rule that a missing `org_id` filter is acceptable" is.

## Final Review

After the last wave merges:

1. `git fetch --no-tags origin <default-branch>`, then
   `MERGE_BASE=$(git merge-base origin/<default-branch> HEAD)`, then
   `scripts/review-package PLAN_FILE "$MERGE_BASE" HEAD` to build the
   whole-branch diff package. **The remote ref, never the local one** — the
   shared checkout's copy of the default branch drifts both ways (ahead by
   commits nobody pushed, behind by merges nobody pulled), and a merge-base
   taken against a drifting ref scopes the final review to the wrong range,
   silently reviewing too much or too little.
2. Dispatch `pitcall:requesting-code-review` on the most capable
   available model, pointed at the diff package and at the ledger's
   deferred-minor and parked lines.
3. **One fix dispatch** carrying the complete findings list — never one
   fixer per finding.
4. Exactly one scoped re-review of the fix diff, using `re-review-prompt.md`.
5. Adjudicate any residual findings per Automation boundary above, then go to
   **Phase 3**. Do not hand off to
   `pitcall:finishing-a-development-branch` — see below for why.

The whole-branch review is load-bearing, not a formality: on a real run it
caught a hole spanning two modules that all four per-task reviews had passed,
each task's diff being correct in isolation and their combination not.

## Phase 3 — Ship and clean up

Input: a plan branch whose every wave integrated and whose whole-branch review
is clean. Output: a merged PR and nothing left behind.

Run `scripts/wdd-finish`, from the **plan worktree**:

```bash
scripts/wdd-finish check   PLAN_FILE   # verify only, no side effects
scripts/wdd-finish ship    PLAN_FILE   # check, push, open PR
scripts/wdd-finish merge   PLAN_FILE   # receipt + green -> merge, then sync main
scripts/wdd-finish cleanup PLAN_FILE   # task branches, worktrees, workspace
```

`check` reads the ledger and refuses on any of: a `Wave N: dispatched` with no
matching `integrated`; a `BLOCKED` entry; a ledger whose first line names a
different plan. It then verifies, against git rather than against the ledger,
that **every task branch is an ancestor of the plan branch** — an integrator
that died after writing its line leaves the claim without the merge, and
nothing else in the workflow would notice.

`ship` and `cleanup` take the base branch from the project config's
`default_branch` — the same source Phase 2 step 1 uses, and never `origin/HEAD`,
which is a clone-time cache that survives a remote renaming its default branch
and is absent entirely from an `init`+`fetch` checkout. `cleanup` gates branch
deletion on that ref, so a stale name that is still a live branch would decide
the fate of the only copy of a task's work. Both refuse rather than guess.

`ship` pushes and opens the PR, and stops there. `merge` is what lands it, and
what it replaces is a human remembering that the branch was validated. That
bound holds until the first time it does not, and nothing observes the failure
— so `merge` is gated on **evidence** instead.

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
   — not this checkout's HEAD, not a sha resolved earlier in the run;
2. checks the receipt for that sha **before waiting on anything**. Refusing
   early costs nothing; refusing after the wait means having spent ten minutes
   on checks for a branch that could never have merged;
3. waits for the config's `required_check` to go green, **bounded** — 30
   minutes, polled every 20s, overridable in seconds with `WDD_CHECK_TIMEOUT`
   and `WDD_CHECK_POLL`. A bound that expires reports *not merged, still
   running* and exits 3, having changed nothing. Anything that is not an
   outright success — failed, skipped, cancelled, or a check that never appears
   — is not a pass;
4. merges with `gh pr merge --merge --match-head-commit <sha>`,
   **server-side**. A local `git merge` runs zero checks and moves HEAD in a
   checkout other sessions are reading. Not a squash: that mints a new commit,
   so what lands is a sha nothing validated, and it also makes `cleanup`'s
   ancestry test permanently false. **The head pin is the other half of the
   receipt** — see below;
5. fast-forwards the main checkout, then runs the project's optional
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
Either remedy changes the head sha, so the lane has to run again: the receipt
does not carry across.

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
it also keeps the receipt honest: updating a stale branch changes its head sha,
which invalidates the receipt along with it.

`cleanup` runs only once the plan branch is an ancestor of the default branch
on `origin`; before that, deleting a task branch would destroy the only copy of
its work.

### Why not pitcall:finishing-a-development-branch

Its option 1 runs `git checkout <base>` and `git pull` **in the main
checkout**, which concurrent sessions are using. A real run measured the base
branch moving twice in the minutes between that skill presenting its menu and
the controller acting, with another session's uncommitted edits in the tree
throughout. Its option 2 is safe, and WDD used to say "take option 2" — but
that is a request, and requests lose: measured on the project this skill grew
in, three separate written rules were argued past, silently skipped, or left
unrun within one week. Each was fixed only by making the wrong thing
mechanically unreachable. `wdd-finish` refuses to run outside a plan worktree,
so the shared checkout is never in reach — with one deliberate exception, the
`--ff-only` sync at the end of `merge`, which cannot create a commit and
reports rather than repairs when it will not apply.

It also never covered cleanup. This document said task branches "persist until
the final PR"; nothing then deleted them. Measured on one repository: 33 task
branches on disk, 20 of them already merged.

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
