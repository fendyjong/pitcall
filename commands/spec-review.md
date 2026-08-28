---
description: Final pass on a spec/plan/design doc — find ambiguities, redundancies, inconsistencies, gaps and clarity problems, then fix them in place so the spec ships clean
argument-hint: [path to spec | branch | PR# | blank = most recent spec touched] [--review-only]
allowed-tools: Read, Edit, Write, Grep, Glob, AskUserQuestion, TodoWrite, Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(gh pr view:*), Bash(gh pr diff:*), Bash(ls:*), Bash(find:*)
---

Take a specification through a **final pass before implementation**: find every ambiguity,
redundancy, inconsistency, gap and clarity problem — then **fix them in the document**, so what you
hand back is a clean spec, not a list of complaints.

## Target

`$ARGUMENTS`

Resolve the target:
- A file path or glob → that document set.
- A branch name, `PR#`, or `HEAD` → the spec/design/plan docs changed in that diff.
- Empty → the spec this session just produced. If there isn't one, find candidates
  (`docs/**/*.md`, `specs/**`, `*plan*.md`, `*design*.md`, `*spec*.md`, most recently modified;
  check `git status` and `git log -1 --name-only`). More than one plausible → list them and ask
  which. Do not guess.

If `--review-only` appears in the arguments, run Phase 1 and stop — report, change nothing.

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
  codebase is marked `unverified`, with what would settle it.
- **A clean spec gets a short report.** Never manufacture findings to fill the template.

Now **sort every finding into exactly one bucket** — this is the whole safety mechanism of Phase 3:

- **MECHANICAL** — the correct fix is determined by the document itself: wording an ambiguity to
  match what the spec plainly intends elsewhere, deleting a duplicate in favour of the canonical
  statement, aligning a name/field/status to its dominant spelling, fixing a contradiction where one
  side is clearly the stale one, restructuring for clarity, nits. **You apply these.**
- **DECISION** — the fix requires choosing something nobody has chosen yet: the retry policy, the
  rollback plan, who is authorised, the timeout value, which of two genuinely contradictory
  statements is right. **You do not know the answer, and neither does the document.** These go to
  Phase 2.

When unsure which bucket, it is a DECISION. Inventing an answer is the one failure mode that makes
the spec worse than leaving it broken: an invented policy reads as ratified and nobody ever revisits it.

---

# Phase 2 — Ask (only for DECISION findings)

Ask the user about the DECISION findings **in one batch**, before touching the file. Use
`AskUserQuestion` where the choice is between concrete options — give the real options with their
consequence, not "what should we do?". Cluster related decisions into one question when they share
an answer. Do not ask about MECHANICAL findings; do not ask permission to fix them.

If the user answers → their answer is what gets written, in their words where they gave words.
If the user skips, defers, or says "leave it" → that finding is **not** silently dropped; it becomes
an explicit marker in Phase 3.

Skip this phase entirely when there are no DECISION findings.

---

# Phase 3 — Fix

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
- **Only the spec.** Do not write code, do not change other docs, do not touch the tracker or open
  issues, do not commit. Leave the change in the working tree for the user to review.

---

# Phase 4 — Verify

Re-read the document as edited, whole, from the top, as a reader who wasn't here:

- Did every Must-fix and Should-fix finding actually get resolved in the text?
- Did the edits introduce a *new* inconsistency — a term now spelled two ways, a reference to a
  paragraph you deleted, a summary that no longer matches its section?
- Does any requirement, number, or caveat from the original no longer appear anywhere?
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

## Left open (N)
Only decisions the user declined to settle, now marked **OPEN:** in the doc.
One line each: the question, and what it blocks.

## Not fixed (N)
Findings deliberately left alone — out of scope, or the fix belongs in a different document.
One line each with the reason and where it belongs.
```

Omit any section that is empty. If the spec was already clean, say so in two lines and stop —
do not edit a document that did not need editing.
