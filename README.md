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

## Filing a solution-ready issue

An issue whose body already carries its own fix can be labelled `solution-ready`, and
`brainstorming` will route it without a human approval gate. Two things are required, and the
second is not optional:

**The `solution-ready` label.** Only a user with triage or write permission can apply one, so the
label is what authorizes an unattended run. It never asserts that the fix is correct.

**A `## Failing check` section**, whose first fenced block is a command that **fails today and
passes when the issue is done**, followed by an `Expected today:` line recording the failure as you
observed it, with the distinguishing text in backticks:

````markdown
## Failing check

```
python3 -m pytest tests/test_widget.py -q
```

Expected today: FAILS with `AssertionError: 4 != 5`
````

The heading is matched literally: `## Failing check`, that spelling and that level. `### Failing
check` and `## Failing Check` are not found, and the run refuses for want of a section rather than
explaining itself.

**The check runs without a shell** — one program plus its arguments. No pipes, redirects, `&&`,
`;` or `cd`: those are shell syntax, and a check carrying one is refused rather than run with the
operator passed through as a literal argument. A check that genuinely needs a shell needs a script,
which the fenced block can name instead — a script in the project the check runs against,
`./tools/check-widget.sh`, not one from this plugin. The check is also bounded to **300 seconds**;
a check that outruns that is a refusal, because an unmeasured check is never a passed one.

`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/solution_ready.py <issue>` runs that command and reads
the result both ways. It **fails as recorded** → the classification is verified and the work
proceeds. It **passes** → the issue is already resolved, and it is closed as completed with the
command's output as evidence. It **fails differently**, or the backticked fragment is missing →
the run refuses, and the issue takes the ordinary path with a human.

The three outcomes are told apart by exit status, not by reading the prose: **0** proceed, **3**
closed (printing `solution-ready: closed`), **1** refused with the reason on stderr. Close is a
do-NOT-proceed outcome, which is why it is not 0.

The backticked fragment is what makes the comparison mechanical rather than a judgement, and an
`Expected today:` line without one reads as *no failure was ever observed* — which is a refusal, not
a detail.

## License

Licensed under the MIT License — see `LICENSE`. This plugin vendors skills from an
upstream open-source project under the same license; see `ATTRIBUTION.md` for what was
taken and how it was adapted.
