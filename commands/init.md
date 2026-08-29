---
description: Write .pitcall/config.json by executing this plugin's own configuration reference, key by key, proposing an evidence-backed value for each key this project justifies
argument-hint: [--force]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(git rev-parse:*), Bash(git symbolic-ref:*), Bash(rm:*), Bash(gh pr list:*), Bash(gh api:*)
---

Configure this project for the plugin by writing `.pitcall/config.json`. This command
carries no opinion of its own about what any key means — `${CLAUDE_PLUGIN_ROOT}/docs/configuration.md`
is the one place that is written down, and this command's whole job is to **execute that
file**, never to restate it. Where this command's text and
`${CLAUDE_PLUGIN_ROOT}/docs/configuration.md` disagree about a key's meaning, the doc is
right and this file is the one that's wrong.

This command executes with its **current directory set to the project being configured**,
never to this plugin's own checkout — `${CLAUDE_PLUGIN_ROOT}` is the one spelling that
resolves to this plugin's own installed directory regardless of where it was installed or
which project it is configuring. Every other path this command names (`.pitcall/config.json`,
`pitcall.config.json`, `.gitignore`) is deliberately bare, because those are meant to resolve
in the project being configured — only the reference to this plugin's own doc needs the
qualifier.

## Arguments

`$ARGUMENTS`

The only argument this command recognises is `--force`. Its presence or absence is what
Step 1 branches on.

---

# Step 1 — Refuse to overwrite

`${CLAUDE_PLUGIN_ROOT}/docs/configuration.md`'s "Where the config lives" section names both
places a config can exist: `.pitcall/config.json` and the legacy root `pitcall.config.json`.
Resolve the checkout root with `git rev-parse --show-toplevel` and check both paths under it.

If either exists and `--force` is **not** in `$ARGUMENTS`: stop here. Report which path you
found, and what passing `--force` would do to it — overwrite it in place if it is already
`.pitcall/config.json`, or migrate it (Step 8) if it is the legacy `pitcall.config.json`. Do
not read further, do not touch `.gitignore`, do not write anything.

If `--force` is present, continue. Remember which path was found, and whether it was the
legacy `pitcall.config.json` — Step 8 needs both facts, because overwriting the legacy file
in place is not an option: only `.pitcall/config.json` is ever written, so a legacy config
found here is migrated, not overwritten.

---

# Step 2 — Read the reference once

Read `${CLAUDE_PLUGIN_ROOT}/docs/configuration.md` in full before doing anything below. Every
key named in Steps 3, 4, and 6 is one of its keys; its meaning, what reads it, and how to
recognise a right value for it all live there. This command only sequences the reading, key
by key, and says where to gather evidence from — it carries no second copy of what a key
means.

---

# Step 3 — Resolve `default_branch` from a query

Run `git symbolic-ref refs/remotes/origin/HEAD`. On success, strip the
`refs/remotes/origin/` prefix — the remainder is the value, and it needs no further
justification because it came from a query, not a guess.

On failure (no `origin` remote, or `origin/HEAD` never set — an initial clone or `git remote
set-head origin -a` is what normally sets it), do not fall back to `main` or `master` by
convention. Leave `default_branch` unset and record that the query failed and why.

---

# Step 4 — Propose the remaining required keys

For `bringup`, `validate`, `required_check`, `worktree_dir`, and `plan_dir`, follow "How to
recognise the right value in a repository that has never seen this tool" under
`${CLAUDE_PLUGIN_ROOT}/docs/configuration.md`'s Required keys section. For each key:

- Look for the evidence that section describes — a CI workflow under
  `.github/workflows/`, a `Makefile`, `package.json` scripts, a README's own contributor
  instructions, an existing `.gitignore` entry, an existing docs/design directory.
- Found it → propose the value together with the evidence, in this shape: *"`required_check`:
  the job that `needs:` every other job in `.github/workflows/ci.yml`"* — never a bare value.
  A value without its reasoning cannot be checked.
- `bringup` specifically: if what you read shows nothing needs to be running first, that is
  a resolved answer — propose `null` explicitly, rather than leaving the key unset. Only
  leave it unset when you cannot tell either way.
- `worktree_dir` and `plan_dir` specifically: **never leave either unset**, even when the
  project gives you nothing to stand on. Neither is in `lane_config.py`'s `REQUIRED_KEYS`, so
  — unlike `bringup`, `validate`, and `required_check` — an unset one does not fail at
  `load_config()` time; it fails much later, the first time a skill asks `project-config
  --scalar` for it, deep inside a wave that has already started. Found project-specific
  evidence → propose it exactly as above. Found nothing → propose this tool's own convention
  instead of leaving the key unset: `.worktrees/` for `worktree_dir`, `docs/plans/` for
  `plan_dir`. State plainly in your output when a value is this tool's convention rather than
  evidence from the project, so the difference is never hidden inside a value that merely
  looks justified.
- Found nothing you would stand behind, for `bringup`, `validate`, or `required_check` → leave
  the key unset and record what you looked at. A required key left unset here is not this
  command's defect: it makes `load_config()` fail loudly the first time anything reads the
  config, which is the correct failure — better than a silently wrong guess standing in for it.

---

# Step 5 — Never propose `outbound_allowlist`

Leave it unset unconditionally, regardless of what the repository looks like — including a
workflow file that looks end-to-end-shaped, which is exactly where the temptation to infer
it lives. State this explicitly in your output: that the key is left unset, and why — it is
the list of external destinations an end-to-end suite may contact, and a wrong value here
sends real traffic to a party that never consented. Do not write `[]` on the project's
behalf either: an empty list is the project's own claim that it contacts nothing, not a
default this command is entitled to make for it.

---

# Step 6 — Propose the remaining optional and declared keys

For `regenerated_paths`, `refresh_commands`, `migration_homes`, `teardown`, `branch_prefix`,
`backlog_milestone`, and `claim_expiry_hours`: follow each key's own "how to recognise the
right value" guidance in `${CLAUDE_PLUGIN_ROOT}/docs/configuration.md`. Propose with evidence
exactly as in Step 4, and leave unset and record what you looked at wherever the project gives
you nothing to stand on. (`worktree_dir` and `plan_dir` moved to Step 4 — both are mandatory
in practice, so neither is ever left unset the way these keys are.)

---

# Step 7 — Write or repair `.gitignore`

Only `.pitcall/config.json` is tracked; nothing else under `.pitcall/` is. Check the
checkout root's `.gitignore` for exactly two lines: `.pitcall/*` and `!.pitcall/config.json`.

- Neither present → append both.
- A bare `.pitcall/` entry, or any other pattern that would also ignore `config.json` — the
  state most existing adopters are in → replace it with the two lines above.
- Already correct → leave it alone.

Also check `.gitignore` for an entry covering the `worktree_dir` value resolved in Step 4 (with
or without a trailing slash — an existing entry counts regardless of which spelling wrote it).
`wave-worktree`'s own guard refuses to create a worktree (exit 4) unless the configured
directory is git-ignored in this clone, so a config this command writes without also covering
this here hands the project a `worktree_dir` that the very next `wave-worktree create` refuses
to use. Missing → append an entry for it. Already covered → leave it alone.

Record what you changed for both checks above, or that nothing needed changing. A setup step
left to a human's memory is the same class of defect as a key left to a guess.

---

# Step 8 — Write the config

Write `.pitcall/config.json` as pretty-printed JSON, containing exactly the keys you
resolved a value for in Steps 3, 4, and 6 (including any resolved explicitly to `null`).
Every key left unset is simply absent from the file; never write a placeholder for it.

If Step 1 found the legacy `pitcall.config.json` (this only happens under `--force`, since a
first run with nothing at either location has nothing to migrate), this run is a migration:
after writing `.pitcall/config.json`, **delete the legacy file.** Never leave both in place —
two configs present at once is exactly the loud failure
`${CLAUDE_PLUGIN_ROOT}/docs/configuration.md`'s "Where the config lives" section defines
(`lane_config.load_config()` raises on it rather than picking one), and `--force` recreating
that failure beside the file it was meant to replace would be worse than refusing outright.
Deleting the legacy file here is the one case this command removes anything. State the
migration in your output, naming both paths — a migration nobody can see is indistinguishable
from a file that went missing.

---

# Output

Report, in this order:

1. Every key you set, each with the evidence it rests on (Step 3's query counts as its own
   evidence; a `worktree_dir` or `plan_dir` set to this tool's own convention rather than
   project evidence must say so plainly, not read as though it were justified by the project).
2. That `outbound_allowlist` was left unset, and why — Step 5's wording, not a summary of it.
3. Every other key left unset, and what you looked at and did not find.
4. Both `.gitignore` changes from Step 7 (the `.pitcall/` lines and the `worktree_dir` entry),
   or that neither was needed.
5. If Step 8 migrated a legacy config, say so explicitly, naming both paths — the legacy file
   that was removed and the new one that replaced it.

If Step 1 stopped the command, report only that: which config exists, and that `--force`
would migrate or overwrite it (per Step 1's distinction).
