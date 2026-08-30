---
description: Final pass on a spec/plan/design doc — find ambiguities, redundancies, inconsistencies, gaps and clarity problems, then fix them in place so the spec ships clean
argument-hint: [path to spec | branch | PR# | blank = most recent spec touched] [--review-only]
allowed-tools: Read, Edit, Write, Grep, Glob, AskUserQuestion, TodoWrite, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git grep:*), Bash(git ls-files:*), Bash(grep:*), Bash(wc:*), Bash(gh pr view:*), Bash(gh pr diff:*), Bash(ls:*), Bash(find:*)
---

Take a specification through a **final pass before implementation**: find every ambiguity,
redundancy, inconsistency, gap and clarity problem — then **fix them in the document**, so what you
hand back is a clean spec, not a list of complaints.

## Target

`$ARGUMENTS`

**The spec is a comment on its work issue, not a file — except on the solution-ready path, where
the issue BODY is the spec.** An issue carrying the `solution-ready` label has no spec comment by
design: its body is the authority, and it is what you read, fix and PATCH.

Resolve the target:
- An issue number or issue URL → the spec comment on that issue — unless it carries the
  `solution-ready` label, in which case see the last bullet.
- A comment URL or comment id → that comment directly.
- Empty → the issue this session is working. The branch name encodes it:
  `<branch_prefix><issue>-<slug>` (`branch_prefix` already includes its own
  separator), so `git rev-parse --abbrev-ref HEAD` names the issue.
- Nothing resolves → list the candidates and ask which. Do not guess.
- The issue carries the `solution-ready` label → its **body** is the spec. Do not look for a
  `# Spec:` comment; there is none, and finding one would mean the issue is on the wrong path.

Read the labels before resolving anything else — every bullet above turns on whether
`solution-ready` is present, and an agent that cannot tell falls back to the comment path and
silently bypasses the whole route:

```bash
gh issue view <n> --repo <owner>/<repo> --json labels --jq '.labels[].name'
```

Find the spec **by its `# Spec:` first line, never by position** — comments accumulate, so the
newest is not the spec and neither, once anyone has replied, is the first:

```bash
gh api "repos/<owner>/<repo>/issues/<n>/comments" \
  --jq '.[] | select(.body|startswith("# Spec:")) | .id'
```

**Never reach for `gh issue comment --edit-last`.** It targets the most recent comment by the
current user, which is the spec only until someone replies — after that it silently edits the wrong
comment, and the spec is left untouched while the report says it was fixed.

If `--review-only` appears in the arguments, run Phases 1 and 2 and stop — report, change nothing.

Read the **entire** target before writing a single finding. Also read whatever the spec cites as
authority (referenced designs, schemas, ADRs, the repo's `CLAUDE.md` / `AGENTS.md`) when a finding
depends on it. Do not review from a skim, and do not review from memory of this conversation — the
document has to stand alone for a reader who wasn't here.

---

# Phase 1 — Find

1. **Ambiguity** — a sentence two competent engineers would implement differently. Undefined terms,
   pronouns with no clear referent, "should"/"may"/"appropriately"/"as needed"/"handle gracefully"
   where a decision belongs. Passive voice hiding *who* does the thing. Numbers with no unit,
   timezone, or precision. "Fast", "soon", "large" with no threshold.
2. **Redundancy** — the same requirement in two places (→ they *will* drift; pick the canonical
   one), a section restating what a linked doc already owns, boilerplate carrying no decision,
   three examples where one teaches the rule.
3. **Inconsistency** — two statements that cannot both be true; a name/field/endpoint/status spelled
   differently across sections; prose contradicting a diagram, table, schema, or example; the spec
   contradicting the codebase or a doc it claims to follow; ordering or state transitions that
   don't line up.
4. **Gaps** — the expensive category, invisible on a read-through because you are looking for what
   is *absent*:
   - error paths, timeouts, retries, partial failure, and what the caller sees for each;
   - concurrency: two at once, out-of-order arrival, duplicate delivery, idempotency;
   - migration & rollout: existing data, backfill, rollback, and the window where old and new both live;
   - authz / tenancy / trust boundary: who may call this, whose data it may touch, which input is
     attacker-controlled and where it gets validated;
   - observability: how anyone knows this is working or broken in production;
   - limits: empty, one, max, unicode, null vs absent, clock skew, money/rounding;
   - exit criteria: what test or measurement makes this "done", and who decides.
5. **Clarity** — structure fighting the reader: the decision buried under context, prose that wanted
   a table, a table that wanted prose, undefined jargon on first use, ordering that forces a forward
   reference, length not earning itself.

Judgment rules:
- **Cite, don't paraphrase.** Every finding quotes the exact offending text with its heading. A
  finding the reader cannot locate is not a finding.
- **Name the failure.** Not "this is vague" — *"implemented as X or Y, both defensible; X breaks Z"*.
  Cannot name a concrete way it goes wrong → drop it.
- **Open questions ≠ gaps.** A decision the spec explicitly defers and marks deferred is a ledgered
  risk. One it silently never makes is a gap. Say which.
- **No scope creep.** Review the spec that exists; don't design a better one. "I'd have done this
  differently" is not a finding. "This cannot work because ___" is.
- **Uncertainty stated, not hidden.** A finding resting on an unverified assumption about the
  codebase is marked `unverified`, with what would settle it — and Phase 2 is where you settle
  it. A mark that survives into the report is one you could not check, never one you did not.
- **A clean spec gets a short report.** Never manufacture findings to fill the template.

Now **sort every finding into exactly one bucket** — this is the whole safety mechanism of Phase 4:

- **MECHANICAL** — the correct fix is determined by the document itself: wording an ambiguity to
  match what the spec plainly intends elsewhere, deleting a duplicate in favour of the canonical
  statement, aligning a name/field/status to its dominant spelling, fixing a contradiction where one
  side is clearly the stale one, restructuring for clarity, nits. **You apply these.**
- **DECISION** — the fix requires choosing something nobody has chosen yet: the retry policy, the
  rollback plan, who is authorised, the timeout value, which of two genuinely contradictory
  statements is right. **You do not know the answer, and neither does the document.** These go to
  Phase 3.

When unsure which bucket, it is a DECISION. Inventing an answer is the one failure mode that makes
the spec worse than leaving it broken: an invented policy reads as ratified and nobody ever revisits it.

---

# Phase 2 — Check (the spec against the tree)

Phase 1 read the document; this phase leaves it. Every claim the spec makes about anything outside
itself — a count, another file, a caller, a lookup — gets checked by **running a command**, and the
output wins. A claim you did not check is reported as unchecked, never as fine.

Each check below names something you type. "Be rigorous" is what Phase 1 already asks for, and it is
not what catches these.

1. **Measure every number.** Line counts, file counts, sizes, "N of M", percentages, "about half".
   `wc -l <paths>`, `git ls-files <path> | wc -l`, `git grep -c '<pat>' -- <path>` — then compare
   with what the spec says. A number carried from memory is a guess wearing precision, and on the
   page it is indistinguishable from a measured one.
2. **Grep every claim about another artefact, and pair each sweep with a known-positive control.**
   "X already does Y", "there is no more Z", "that logic lives in W" are searches, not assertions:
   `git grep -n '<term>' -- <path>`. Every path, filename and directory the spec names is a claim of
   the same kind — `ls <path>`, never recall, because a layout that has been reorganised reads
   exactly like one that has not. Then run the same sweep for a term you know is present. **The
   control is the check** — a sweep that found nothing and a sweep that never ran produce the same
   output, and both spell `0`. Not hypothetical: a sweep across twenty-two commits reported `0 hits`
   for every one of them because the loop running it had lost `git` from its `PATH`. A sweep whose
   control comes back empty measured nothing; repair it and run it again.
3. **Name each mechanism's caller, and check that the caller can satisfy it.** For every "the system
   records / writes / locks / reads …", answer two questions: *who invokes this*, and *can they do
   what the spec asks?* Read the caller; do not reason about it. A mechanism specified against no
   caller survives every reading pass, because there is nothing wrong with it as prose — it is
   merely impossible.
4. **For every lookup or resolution, name an input on which its cases diverge.** "Resolve the X",
   "find the current Y", "the Z for this run" is often several questions wearing one name, and they
   agree in the environment the author is sitting in — which is exactly why the author cannot see
   it. Name a concrete input where the answers differ. Failing to find one is a finding, not a
   clean result.

Findings here sort into the same two buckets as Phase 1. There is no third:

- A **measurement that contradicts the spec** is MECHANICAL — the tree is the authority. Correct the
  number to what you measured, and report what you ran.
- A **mechanism no caller can satisfy**, or a **lookup that turns out to be two questions**, is a
  DECISION. The fix is a design choice nobody has made yet, and writing a plausible one here ratifies
  an impossible mechanism as though someone had chosen it.

Why a reading pass cannot substitute for this. Each of the following shipped in a reviewed spec:

- a line-count total for a set of files, given to the digit and wrong by ninety-nine lines;
- "those rules have already moved to the other component" — the other component contained none of them;
- liveness defined as a recorded process id, whose only writer is a short-lived subprocess: the value
  is dead by the time anyone reads it;
- one named function resolving "the project", which is two questions — which lock, which checkout —
  whose answers coincide in a main checkout and diverge in a linked worktree;
- "the run writes a receipt", silent on when the recorded commit id is read back and whether the tree
  must be clean; both load-bearing.

**Every one of them reads perfectly, because every one is internally consistent and externally
false.** There is nothing in the document to notice. Three were caught by a later review and two by
an implementer who tried to build the thing and could not — none by reading the spec. The four checks
above are earlier and cheaper than either.

---

# Phase 3 — Ask (only for DECISION findings)

Ask the user about the DECISION findings **in one batch**, before touching the file. Use
`AskUserQuestion` where the choice is between concrete options — give the real options with their
consequence, not "what should we do?". Cluster related decisions into one question when they share
an answer. Do not ask about MECHANICAL findings; do not ask permission to fix them.

**On the solution-ready path a DECISION finding is proof the label was wrong.** Do not ask the
question and continue: remove the label, say which finding overturned it, and demote the issue to a
brainstorming session with the human. A label that survived a contradicting finding would be worse
than no label at all.

```bash
gh issue edit <n> --repo <owner>/<repo> --remove-label solution-ready
```

If the user answers → their answer is what gets written, in their words where they gave words.
If the user skips, defers, or says "leave it" → that finding is **not** silently dropped; it becomes
an explicit marker in Phase 4.

Skip this phase entirely when there are no DECISION findings.

---

# Phase 4 — Fix

Edit the spec in place. Rules:

- **Minimal diff.** Change the sentences that are wrong. Do not rewrite sections that work, do not
  reformat untouched text, do not restructure the document because you'd have organised it
  differently. A large diff means you overstepped.
- **The author's voice, not yours.** Match the doc's existing tone, heading style, terminology,
  person, and formatting conventions. A reader must not be able to tell which paragraphs you touched.
- **Fix, don't annotate.** No "TODO: clarify", no review comments left in the prose, no changelog of
  what you fixed inside the document. The fixed text is the fix.
- **Redundancy = delete, and make the survivor authoritative.** Keep the canonical statement, delete
  the copies, and point anything that referenced a deleted copy at the survivor. Never leave both
  with a note saying they should agree.
- **Unanswered DECISIONs get an honest marker,** in the spec's own format:
  `**OPEN:** <the question> — blocks <what it blocks>. Decide before <the milestone it gates>.`
  Placed where the decision belongs, not in a footer. This is the *only* place a marker is allowed,
  and only for questions the user declined to settle.
- **Never invent a decision.** No default retry counts, no assumed timeouts, no guessed authz model,
  no plausible-sounding rollback plan. If it wasn't in the doc and the user didn't say it, it does
  not get written as though it were decided.
- **Preserve every load-bearing detail.** Tightening prose must not drop a constraint, a number, an
  edge case, or a caveat. Cutting length is not worth losing a requirement.
- **Update what the edits invalidate** — a table of contents, a summary section, cross-references,
  a diagram caption, an example that used the old field name. An internally inconsistent "fixed"
  spec is a worse outcome than the original.
- **Bump the `**Version:** N` header and reset `Status:` to draft, in the same edit.** Approval
  names a version, so an edit after approval has to re-open that gate. Leaving the version alone
  silently transfers an approval onto text nobody approved — the failure state sitting exactly where
  the safe state should be.
  A solution-ready issue body carries no `**Version:**` header — it was never approved at a version,
  because it was never put to a human. Nothing to bump there; leave the body's own headings alone.
- **Only the spec.** Do not write code, do not change other docs, do not touch any other comment or
  issue, do not commit. The edit lands on the comment itself:

  ```bash
  gh api --method PATCH "repos/<owner>/<repo>/issues/comments/<id>" \
    -f body="$(cat <edited-spec-file>)"
  ```

  **On the solution-ready path the subject is the issue body, so PATCH the issue instead:**

  ```bash
  gh api --method PATCH "repos/<owner>/<repo>/issues/<n>" \
    -f body="$(cat <edited-spec-file>)"
  ```

  Write the edited text to a scratch file first and PATCH from it — passing a long body inline is
  how quoting mangles a spec.

---

# Phase 5 — Verify

Re-read the document as edited, whole, from the top, as a reader who wasn't here:

- Did every Must-fix and Should-fix finding actually get resolved in the text?
- Did the edits introduce a *new* inconsistency — a term now spelled two ways, a reference to a
  paragraph you deleted, a summary that no longer matches its section?
- Does any requirement, number, or caveat from the original no longer appear anywhere — other
  than a number Phase 2 measured and corrected?
- Does anything now read as decided that nobody decided?

Fix what this pass turns up before reporting. Do not report completion on an unverified edit.

---

# Output

Report to the user (not into the document):

```
## Verdict
<clean | clean except N open decisions | still blocked> — one sentence.

## Fixed (N)
One line each, worst first:
**[ambiguity|redundancy|inconsistency|gap|clarity]** § <section> — <what was wrong> → <what it says now>

## Checked (N)
Every Phase 2 command, including the ones that agreed — the agreements are what show the phase ran.
One line each: `<command>` → <what it returned>; spec said <what>.

## Left open (N)
Only decisions the user declined to settle, now marked **OPEN:** in the doc.
One line each: the question, and what it blocks.

## Not fixed (N)
Findings deliberately left alone — out of scope, or the fix belongs in a different document.
One line each with the reason and where it belongs.
```

Omit any section that is empty — except `## Checked`: a clean verdict with nothing under it is
indistinguishable from a verdict nobody checked. If the spec was already clean, say so in two
lines, keep `## Checked`, and stop — do not edit a document that did not need editing.
