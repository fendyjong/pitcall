# Configuration reference

This is the one artifact that describes the plugin's configuration surface. It has
two readers: a human deciding whether to adopt the tool, and this plugin's `init`
command, which **executes this file** — it reads each key's entry below and
proposes a value from it, rather than restating the meanings in its own prompt.
There is no second copy; if this file and `init`'s behaviour ever disagree, this
file is the one to trust and `init` is the one that is wrong.

**Where the config lives.** `.pitcall/config.json`, tracked. A legacy root-level
`pitcall.config.json` still resolves, with a deprecation warning, for one release;
having both present at once is a loud failure, not a precedence rule.

**The governing rule: presence is checked, never guessed.** The plugin is generic —
it is installed once and drives many projects, so it cannot know your default branch
or what brings your stack up. A key the loader requires and does not find is a loud
failure that names itself immediately; a silently-defaulted key fails much later and
blames something unrelated. Writing a key as JSON `null` is different from omitting
it: `null` is a project saying "there is no such step," and the step is skipped
cleanly. Omitting a *required* key is refused outright.

Every "what reads it" cell below names the actual file(s) that read the key today,
found by grepping the tree (`git grep -lE '\bKEY\b'`, word-bounded — a substring
search reports `worktree_dir` as read in files that only contain the unrelated local
`worktree_dirt` in `scripts/lane.py`, which is exactly how a dead key passed for a
live one in an earlier draft of this document). A cell that names no file is not a
guess; it is what the tree currently contains.

## Required keys

Missing outright, `load_config()` raises immediately and names every missing key at
once. `bringup` and `validate` may still be `null` — required means the *key* must
exist, not that it must do anything.

| Key | Meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `bringup` | Shell command line that brings the project's stack up, run before validation. | Yes — the key must exist. May be `null`. | `scripts/lane.py` (runs it), `scripts/lane_config.py` (checks the key exists). | Key missing entirely: `load_config()` raises, naming it. Key present as `null` or `""`: `lane.py` prints `bringup: none configured, skipping` and moves on — this is the correct value for a project with nothing to bring up. |
| `validate` | Shell command line — the one suite whose pass licenses a merge — run after `bringup`. | Yes — the key must exist. May be `null`. | `scripts/lane.py`, `scripts/lane_config.py`. | Same shape as `bringup`. A run whose `validate` is `null` also never writes a validation receipt, so nothing downstream can treat it as validated. |
| `default_branch` | The branch work is cut from and merged back to. | Yes — must be a non-empty string. | `scripts/lane_config.py` (checks the key exists), `skills/wave-driven-development/scripts/wdd` (reads the value via `project-config --scalar default_branch` to know what to branch from and retarget PRs onto). | Key missing entirely: `load_config()` raises. Key present but empty or non-string: the *reader* refuses (`project-config: default_branch must be a non-empty string ...`) the first time anything asks for it, not at config-load time. |
| `required_check` | Name of the CI status check that must go green before `wdd`'s merge step proceeds. | Yes — must be a non-empty string. | `scripts/lane_config.py`, `skills/wave-driven-development/scripts/wdd` (polls this check by name, bounded, before merging). | Same shape as `default_branch`. |

**How to recognise the right value in a repository that has never seen this tool:**

- `bringup` — the single command a new contributor is told to run before testing
  (start a database, a dev server, a container stack). If nothing needs to be
  running first, the honest value is `null`, not a command that does nothing.
- `validate` — the one command whose exit status this project treats as "this
  change is good." Usually what the project's main CI job runs. If several suites
  exist, it's the one a required check depends on, not merely the most recent one
  someone ran by hand.
- `default_branch` — resolved by a query, not a guess: `git symbolic-ref
  refs/remotes/origin/HEAD` (or the hosting UI's "default branch" setting). Never
  assume `main` over `master` by convention.
- `required_check` — the job name a merge actually waits on. Read it out of the CI
  workflow file itself (often the job that depends on — `needs:`, in GitHub
  Actions — every other job, so its own success implies theirs), not off a recent
  PR's green checkmark, which can be green for reasons unrelated to what merged.

## Optional keys with a shipped reader

Absence is a clean answer here: the step that reads the key does nothing, so a
project without one skips the step by doing nothing rather than by remembering to
branch on it.

| Key | Meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `outbound_allowlist` | Exact-match list of external destinations (e.g. phone numbers) an end-to-end suite is permitted to actually contact. | No — deliberately excluded from the required-keys check even though it is load-bearing wherever it's used (see "If absent"). | `scripts/lane_config.py`'s `allowlisted()`. | **Not graceful.** If the key is missing and something calls `allowlisted()`, it raises an uncaught `KeyError` — there is no "nothing is allowed" fallback built in. Write `[]` explicitly if the project truly contacts nothing; the key can stay absent only if no code path in the project ever calls `allowlisted()`. |
| `regenerated_paths` | Tracked paths a git hook rewrites (e.g. a `post-checkout` hook), which are dirtied by `git worktree add` itself, before any task touches them. | No. | `skills/wave-driven-development/scripts/project-config` (list mode), invoked from the per-wave worktree-creation step in `skills/wave-driven-development/SKILL.md` to restore these paths right after creation. | Absent, `null`, and `[]` are the same answer: nothing to restore, so the restore step runs and prints nothing. |
| `refresh_commands` | Shell command lines run in the **main checkout**, best-effort, after `wdd`'s merge step fast-forwards it. | No. | `skills/wave-driven-development/scripts/wdd` (the merge step). | Nothing to regenerate, so the step is skipped. A command listed here that fails is reported but never unwinds the merge — the merge has already happened by the time this runs. |
| `migration_homes` | Directories holding this project's migration files, one entry per independently-numbered sequence. | No. | `skills/wave-driven-development/scripts/project-config` (list mode), read at planning time (`SKILL.md`) to catch two tasks in one wave claiming the same migration number in the same home. | Absent, `null`, and `[]` are the same answer: the project has no migration directories, so the duplicate-number check has nothing to compare and does nothing. |

**How to recognise the right value:**

- `outbound_allowlist` — never inferred, never proposed by `init`. It is asked of a
  human who can vouch for each destination's consent, or left unset. A wrong value
  here sends real traffic to a party that did not agree to receive it.
- `regenerated_paths` — check what the project's own git hooks touch (commonly
  `.git/hooks/post-checkout`, or whatever a hook-install script wires up). No such
  hook means no such paths.
- `refresh_commands` — what a maintainer runs by hand after merging, or what CI
  regenerates and commits back (a lockfile-derived file, a generated index). A
  project with nothing like that has an empty list, honestly.
- `migration_homes` — where migration files actually live on disk (a directory
  holding sequentially-numbered migration files). A project with one database
  behind one schema has exactly one home; a project with two independently
  numbered schemas has two.

## Declared, but nothing reads them yet

These keys exist in `pitcall.config.example.json` and are legitimate places to
record a value, but no shipped code consumes them today. Writing a value here has
no observable effect until the work that reads it lands; that work is specified
elsewhere, not by this file. Unlike the two keys in the next section, nothing here
is standing in for these — there is simply no consumer yet.

| Key | Intended meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `teardown` | Shell command line to tear the stack `bringup` started back down. | No. | Nothing shipped. Appears only in this project's own test fixtures (`skills/wave-driven-development/tests/test_wave_worktree.py`, `tests/test_lane.py`, `tests/test_lane_config.py`) and, coincidentally, the English word "teardown" in an unrelated comment in `scripts/lane.py` — that comment is not a read of this key. | No effect either way. Do not write it expecting a step to run. |
| `branch_prefix` | The prefix a cut branch's name carries ahead of `<issue>-<slug>`, e.g. `feat/`. | No. | Nothing shipped reads the key. One command's prose (`commands/spec-review.md`) already assumes branches are named `<branch_prefix>/<issue>-<slug>` when deriving the current issue from `git rev-parse --abbrev-ref HEAD`, but it does that by convention, not by reading this key. | No effect. Declared for branch-naming work specified elsewhere. |
| `backlog_milestone` | Name of the milestone that holds backlog / not-yet-scheduled work, distinct from a milestone a plan is actively executing against. | No. | Nothing shipped. | No effect. Declared for backlog-triage work specified elsewhere. |
| `claim_expiry_hours` | Hours an issue claim is honoured before another session may treat it as abandoned and take the work over. | No. | Nothing shipped. | No effect. Declared for claim-protocol work specified elsewhere. |

**How to recognise the right value, if you are choosing one ahead of the reader that
will use it:**

- `teardown` — mirror `bringup`: the command that reverses it (`docker compose
  down`, stopping whatever `bringup` started).
- `branch_prefix` — look at how issue-linked branches are already named in this
  project's history (recent merged PR branch names) for a consistent shape.
- `backlog_milestone` — the project's milestone list (e.g. `gh api
  repos/<owner>/<repo>/milestones`) for the one nothing is currently scheduled
  against.
- `claim_expiry_hours` — a convention this tool will own outright rather than a
  project fact to discover; pick a number that comfortably outlasts one working
  session.

## Declared, read from a literal today — being wired by this change

Both keys measured zero readers for the same reason: a hardcoded literal already
does the job the key was declared for, sitting beside code that could read the key
instead and does not. That is a worse state than "not yet built" — the consumer
exists, and simply ignores the key. Wiring these up (removing the literals, reading
the key in their place) is part of this same change, not a deferral.

| Key | Meaning | Required? | Literal it replaces | If absent (today) |
| --- | --- | --- | --- | --- |
| `worktree_dir` | Directory, relative to the project root, that per-task worktrees are created under. | Not today. Will become effectively required once wired (see "If absent"). | `.worktrees/`, hardcoded in `skills/wave-driven-development/scripts/wave-worktree` (path construction and the ignore check) and named in `skills/wave-driven-development/SKILL.md` and `skills/brainstorming/SKILL.md`. | No effect today — the literal is used regardless of what (or whether) this key is set. Once wired, a missing key is a loud failure: no fallback to `.worktrees/` is kept. |
| `plan_dir` | Directory, relative to the project root, that WDD plan documents are saved to. | Not today. Same trajectory as `worktree_dir`. | `docs/superpowers/plans/`, hardcoded in the plan-saving instruction in `skills/wave-driven-development/SKILL.md`. | No effect today, same reason. Once wired, a missing key is a loud failure there too. |

**How to recognise the right value:**

- `worktree_dir` — check `.gitignore`: whatever directory holds per-task worktrees
  must already be ignored there (git refuses to create a worktree inside a
  tracked, non-ignored path), so an existing ignore entry for a worktree-shaped
  directory is usually the answer already in use.
- `plan_dir` — if the project already keeps design documents somewhere (a `docs/`,
  `design/`, or `specs/` directory with a consistent convention), that is the
  answer. A project with no such convention yet is choosing one for the first
  time by setting this key.

## Not a key of this plugin

`premerge_script` is not documented above because it does not exist here: it
appears in at least one project's configuration outside this repository, and
nowhere in this repository — not in code, not in `pitcall.config.example.json`, not
in prose. It was invented against a surface that had never documented what it
actually was. If you find it in a config you're adopting, it does nothing when read
by this plugin; the project that wrote it meant something this plugin does not
implement.
