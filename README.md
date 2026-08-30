# pitcall

A Claude Code plugin: a set of skills, commands, and a validation-lane script for a
disciplined, review-gated development workflow. It packages practices for turning a
spec into a shipped change — planning, parallel execution, code review, and
verification-before-completion — as reusable Claude Code skills.

## Install

Add this repository as a plugin source in Claude Code and enable it, or clone it and
point Claude Code's plugin loader at the checkout. See the skills under `skills/` and
the commands under `commands/` for what each piece does once installed.

## Project configuration

The plugin is generic: it is installed once and drives many projects, so it cannot
guess what brings your stack up or what your default branch is called. Each project
tells it, in a config file — see [`docs/configuration.md`](docs/configuration.md)
for exactly where that file lives.

It is read from the checkout you are standing in, so a linked worktree reads the
copy on its own branch: a branch that changes its validate command is tested with
that command, and the change is visible in its own diff.

Copy `pitcall.config.example.json` into place and edit it, or run `/pitcall:init` to have
the plugin propose it for you — the command reads
[`docs/configuration.md`](docs/configuration.md) itself and executes it, key by key,
rather than restating it. If you ever see

```
lane: no .pitcall/config.json or pitcall.config.json in <checkout> — that is the
checkout resolved from the current directory
```

this file is what it is asking for, and `<checkout>` is where to put it.

**Every key is documented in [`docs/configuration.md`](docs/configuration.md)** — its
meaning, whether it's required, what reads it, what happens when it's absent, and how
to recognise the right value in a project that has never seen this tool.

## Claiming and filing issues

`scripts/claim.py` and `scripts/file.py` are human-run CLIs, invoked from this project's
own checkout (not a project this plugin drives):

```
python3 scripts/claim.py <issue-number> [--session <url>] [--take]
python3 scripts/file.py "<title>" [--body-file <path>]
```

`claim` posts a claim comment (and applies `status_labels.ongoing`, if configured) and
cuts `<branch_prefix><issue>-<slug>` from `default_branch` — refusing if the issue
already carries a live claim, or if more than one branch matches the issue and there
is no way to tell which holds the work. `--session` records the caller's session URL
in the comment, so a later run can recognise its own claim; `--take` reclaims a claim
that reads stale. `file` opens a new issue straight into `backlog_milestone` — never
the milestone in flight. Both refuse loudly, rather than guess, when a key they need
is missing from the config; see [`docs/configuration.md`](docs/configuration.md).

## License

Licensed under the MIT License — see `LICENSE`. This plugin vendors skills from an
upstream open-source project under the same license; see `ATTRIBUTION.md` for what was
taken and how it was adapted.
