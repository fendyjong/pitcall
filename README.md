# pitcall

A Claude Code plugin: a set of skills, commands, and a validation-lane script for a
disciplined, review-gated development workflow. It packages practices for turning a
spec into a shipped change — planning, parallel execution, code review, and
verification-before-completion — as reusable Claude Code skills.

## Install

Add this repository as a plugin source in Claude Code and enable it, or clone it and
point Claude Code's plugin loader at the checkout. See the skills under `skills/` and
the commands under `commands/` for what each piece does once installed.

## Project configuration — `pitcall.config.json`

The plugin is generic: it is installed once and drives many projects, so it cannot
guess what brings your stack up or what your default branch is called. Each project
tells it, in a file named exactly `pitcall.config.json`.

**Where it goes.** Committed at the root of the project you are working on — not in
this plugin's checkout. It is read from the checkout you are standing in, so a linked
worktree reads the copy on its own branch: a branch that changes its validate command
is tested with that command, and the change is visible in its own diff.

Copy `pitcall.config.example.json` to your project root and edit it, or run
`/pitcall:init` to have it proposed for you — the command reads
[`docs/configuration.md`](docs/configuration.md), the full reference for every key,
and executes it rather than restating it. If you ever see

```
lane: no pitcall.config.json in <checkout> — that is the checkout resolved from the
current directory
```

this file is what it is asking for, and `<checkout>` is where to put it.

### Required keys

A missing key is a loud failure, never a default — a silent default would cut a branch
from the wrong base or gate a merge on a check that does not exist, both of which look
like working behaviour right up until they do not. *Presence* is what is checked, not
truthiness: writing `null` is a project saying "there is no such step", and the step is
then skipped.

| Key | What it is |
| --- | --- |
| `bringup` | Shell command line that brings the project's stack up, run inside the checkout before validation. `null` = no such step. |
| `validate` | Shell command line that validates the checkout, run after `bringup`. `null` = no such step. |
| `default_branch` | The branch work is cut from and merged back to, e.g. `main`. Must be a non-empty string — `null` is an error, because a caller asking "which branch?" has no way to proceed without a name. |
| `required_check` | Name of the CI status check that must be green before a PR merges. Non-empty string, same reason. |

### Optional keys

Absence is an answer here, not an error — the workflow step that reads the key simply
does nothing, so a project without one skips the step by doing nothing rather than by
remembering to branch on it.

| Key | Absence means |
| --- | --- |
| `regenerated_paths` | No tracked file is rewritten by a git hook, so nothing needs restoring after a worktree is created. Absent, `null` and `[]` are all the same answer. |
| `migration_homes` | The project has no migration directories, so the duplicate-number check has nothing to compare. Absent, `null` and `[]` are the same answer. |
| `refresh_commands` | Nothing to regenerate after a merge, so `wdd merge` skips the step. Each item is a shell command line, run in the **main checkout** after it fast-forwards, and **best-effort**: the merge has already happened, so a failure here is reported and the run still succeeds. |
| `outbound_allowlist` | Only read by `lane_config.allowlisted()`. A project that sends nothing outbound need not write it; if something does call `allowlisted()` without it, that call fails loudly rather than permitting anything. |
| `teardown` | Nothing. **No shipped command reads this key.** It appears in the test fixtures and is reserved for a future teardown step — do not write it expecting it to run. |

A list-valued key holding something that is not a list is an error rather than an empty
answer: a bare string would word-split at the caller into paths nobody wrote.

## License

Licensed under the MIT License — see `LICENSE`. This plugin vendors skills from an
upstream open-source project under the same license; see `ATTRIBUTION.md` for what was
taken and how it was adapted.
