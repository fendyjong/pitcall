---
description: Deprecated name of /pitcall:setup, kept until 0.17.0 — writes .pitcall/config.json
argument-hint: [--force]
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(git rev-parse:*), Bash(git symbolic-ref:*), Bash(rm:*), Bash(gh pr list:*), Bash(gh api repos/*/milestones)
---

<!-- deprecated(0.16.0): the old name of /pitcall:setup; this file goes in 0.17.0. -->

`/pitcall:init` is deprecated and goes in 0.17.0. Say so in one line:
"pitcall: '/pitcall:init' is deprecated and goes in 0.17.0; use '/pitcall:setup'".
Then do what `${CLAUDE_PLUGIN_ROOT}/commands/setup.md` says, with the same arguments: $ARGUMENTS
