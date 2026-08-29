# Plan Reviewer Prompt Template

Use this template for the one plan review in Phase 1, after your own
self-review checklist and before `plan-tasks --validate` (SKILL.md, Phase 1 —
Self-review, then validate).

It and the validator check different things and neither substitutes for the
other. `--validate` decides structure — wave membership, dependency direction,
path disjointness, migration numbers, model tiers — mechanically, and cannot
read English. This reviewer reads the plan the way an implementer will, and
answers the one question the validator has no access to: could an engineer
follow this without getting stuck?

```
Subagent (general-purpose):
  description: "Review plan document"
  model: [MODEL — REQUIRED: standard tier or better; an omitted model
         silently inherits the session's own]
  prompt: |
    You are a plan document reviewer. Verify this plan is complete and ready
    for implementation.

    **Plan to review:** [PLAN_FILE_PATH]
    **Spec for reference:** [SPEC_FILE_PATH]

    Read both. The spec is the authority the plan argues from; where the two
    disagree, the spec wins and the plan is what needs fixing.

    ## What to Check

    | Category | What to Look For |
    |----------|------------------|
    | Completeness | TODOs, placeholders, incomplete tasks, missing steps |
    | Spec Alignment | Plan covers spec requirements, no major scope creep |
    | Task Decomposition | Tasks have clear boundaries, steps are actionable |
    | Buildability | Could an engineer follow this plan without getting stuck? |

    **Placeholders are plan failures, not style notes.** "TBD", "add
    appropriate error handling", "write tests for the above" with no test
    code, "similar to Task N" instead of the repeated code, a step describing
    what to do without showing how, a reference to a type or function no task
    defines, or a migration filename with an unassigned number. Each one is a
    gap an implementer will fill by guessing.

    **Each task's implementer sees only its own task**, never the whole plan.
    So check that a task is self-sufficient: the exact values, signatures and
    test cases it needs are inside it, and anything it takes from another task
    is named in its `Consumes:` line rather than assumed.

    ## Calibration

    **Only flag issues that would cause real problems during implementation.**
    An implementer building the wrong thing or getting stuck is an issue.
    Minor wording, stylistic preferences, and "nice to have" suggestions are not.

    Approve unless there are serious gaps — missing requirements from the spec,
    contradictory steps, placeholder content, or tasks so vague they can't be acted on.

    Do not review the wave assignment, the dependency graph, or path
    disjointness. A separate validator checks those mechanically, and a second
    opinion on them from here is noise.

    ## Output Format

    ## Plan Review

    **Status:** Approved | Issues Found

    **Issues (if any):**
    - [Task X, Step Y]: [specific issue] - [why it matters for implementation]

    **Recommendations (advisory, do not block approval):**
    - [suggestions for improvement]
```

**Placeholders:**
- `[MODEL]` — REQUIRED: standard tier or better
- `[PLAN_FILE_PATH]` — the plan being reviewed
- `[SPEC_FILE_PATH]` — the spec the plan argues from. If the plan names no
  reachable spec, say so here rather than leaving it blank — a review with no
  spec can check buildability but not coverage

**Reviewer returns:** Status, Issues (if any), Recommendations.

An `Issues Found` verdict is worth acting on rather than arguing with: the
calibration above is deliberately loose, so anything it raises cleared a bar
set at "would cause real problems during implementation." Fix what it raises,
then run `--validate`.
