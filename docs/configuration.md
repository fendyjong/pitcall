# Configuration reference

This is the one artifact that describes the plugin's configuration surface. It has
two readers: a human deciding whether to adopt the tool, and this plugin's `setup`
command, which **executes this file** — it reads each key's entry below and
proposes a value from it, rather than restating the meanings in its own prompt.
There is no second copy; if this file and `setup`'s behaviour ever disagree, this
file is the one to trust and `setup` is the one that is wrong.

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
`worktree_dirt` in `scripts/pit.py`, which is exactly how a dead key passed for a
live one in an earlier draft of this document). A cell that names no file is not a
guess; it is what the tree currently contains.

## Required keys

Missing outright, `load_config()` raises immediately and names every missing key at
once — that's true of the four keys below that `lane_config.py`'s `REQUIRED_KEYS` actually
names. `out_lap` and `validate` may still be `null` — required means the *key* must exist, not
that it must do anything.

| Key | Meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `out_lap` | Shell command line that brings the project's stack up, run before validation. | Yes — the key must exist. May be `null`. | `scripts/pit.py` (runs it), `scripts/lane_config.py` (checks the key exists). | Key missing entirely: `load_config()` raises, naming it. Key present as `null` or `""`: `pit.py` prints `[pit lane] out_lap: none configured, skipping` and moves on — this is the correct value for a project with nothing to bring up. |
| `validate` | Shell command line — the one suite whose pass licenses a merge — run after `out_lap`. | Yes — the key must exist. May be `null`. | `scripts/pit.py`, `scripts/lane_config.py`. | Same shape as `out_lap`. A run whose `validate` is `null` also never writes a pit pass, so nothing downstream can treat it as validated. |
| `default_branch` | The branch work is cut from and merged back to. | Yes — must be a non-empty string. | `scripts/lane_config.py` (checks the key exists), `skills/race/scripts/race` (reads the value via `project-config --scalar default_branch` to know what to branch from and retarget PRs onto). | Key missing entirely: `load_config()` raises. Key present but empty or non-string: the *reader* refuses (`project-config: default_branch must be a non-empty string ...`) the first time anything asks for it, not at config-load time. |
| `required_check` | Name of the CI status check that must go green before `race finish` proceeds. | Yes — must be a non-empty string. | `scripts/lane_config.py`, `skills/race/scripts/race` (polls this check by name, bounded, before merging). | Same shape as `default_branch`. |

**Two more keys are just as mandatory in practice — every race needs both — but neither is
in `REQUIRED_KEYS`, so `load_config()` does not check them. Each is enforced instead by its own
reader, through `project-config --scalar`, the first time a skill asks for it — later than
`load_config()`, and deep inside a lap rather than at the start of a run:**

| Key | Meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `worktree_dir` | Directory, relative to the project root, that per-task worktrees are created under. | Yes in practice — must be a non-empty string. Not checked by `load_config()`. | `skills/race/scripts/lap-worktree` (path construction and the ignore check, via `project-config --scalar worktree_dir`), `skills/race/scripts/race`'s `require_worktree_dir` (used by `pack-down`); named in `skills/race/SKILL.md` and `skills/brainstorming/SKILL.md`. | `load_config()` succeeds regardless — the key isn't in `REQUIRED_KEYS`. `lap-worktree`'s own first line, `project-config --scalar worktree_dir`, exits 2 naming the key: the race cannot create a task worktree at all. |
| `plan_dir` | Directory, relative to the project root, that race plan documents are saved to. | Same shape as `worktree_dir`. | `skills/race/SKILL.md`, via `project-config --scalar plan_dir`. No script reads it directly — the same standard as `migration_homes` below, just required rather than optional. | Same shape as `worktree_dir`: `project-config --scalar plan_dir` exits 2 the first time the race tries to save a plan (Phase 1, step 1). |

**How to recognise the right value in a repository that has never seen this tool:**

- `out_lap` — the single command a new contributor is told to run before testing
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
- `worktree_dir` — check `.gitignore`: whatever directory holds per-task worktrees
  must already be ignored there (git refuses to create a worktree inside a
  tracked, non-ignored path), so an existing ignore entry for a worktree-shaped
  directory is usually the answer already in use. A project with no such entry
  yet is choosing one for the first time by setting this key — `.worktrees/` is
  this tool's own convention absent a project-specific reason to pick something
  else.
- `plan_dir` — if the project already keeps design documents somewhere (a `docs/`,
  `design/`, or `specs/` directory with a consistent convention), that is the
  answer. A project with no such convention yet is choosing one for the first
  time by setting this key — `docs/plans/` is this tool's own convention absent a
  project-specific reason to pick something else.

## Optional keys with a shipped reader

Absence is a clean answer here: the step that reads the key does nothing, so a
project without one skips the step by doing nothing rather than by remembering to
branch on it.

| Key | Meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `outbound_allowlist` | Exact-match list of external destinations (e.g. phone numbers) an end-to-end suite is permitted to actually contact. | No — deliberately excluded from the required-keys check even though it is load-bearing wherever it's used (see "If absent"). | `scripts/lane_config.py`'s `allowlisted()`. | **Not graceful.** If the key is missing and something calls `allowlisted()`, it raises an uncaught `KeyError` — there is no "nothing is allowed" fallback built in. Write `[]` explicitly if the project truly contacts nothing; the key can stay absent only if no code path in the project ever calls `allowlisted()`. |
| `regenerated_paths` | Tracked paths a git hook rewrites (e.g. a `post-checkout` hook), which are dirtied by `git worktree add` itself, before any task touches them. | No. | `skills/race/scripts/project-config` (list mode), invoked from the per-lap worktree-creation step in `skills/race/SKILL.md` to restore these paths right after creation. | Absent, `null`, and `[]` are the same answer: nothing to restore, so the restore step runs and prints nothing. |
| `refresh_commands` | Shell command lines run in the **main checkout**, best-effort, after `race finish` fast-forwards it. | No. | `skills/race/scripts/race` (`race finish`). | Nothing to regenerate, so the step is skipped. A command listed here that fails is reported but never unwinds the merge — the merge has already happened by the time this runs. |
| `migration_homes` | Directories holding this project's migration files, one entry per independently-numbered sequence. | No. | `skills/race/scripts/project-config` (list mode), read at planning time (`SKILL.md`) to catch two tasks in one lap claiming the same migration number in the same home. | Absent, `null`, and `[]` are the same answer: the project has no migration directories, so the duplicate-number check has nothing to compare and does nothing. |
| `status_labels` | The project's own names for the claim states, as `{ongoing, in_review, blocked}`. The plugin applies them; the project names them. | No. | `scripts/tracker.py`'s `status_label()`: via `scripts/enter.py`, which applies `ongoing`; and via `tracker.py status-label` from `skills/race/scripts/race`, whose `grid` swaps `ongoing` for `in_review` on the issue the branch names, once the PR body closes it, and whose `finish` removes both once that PR lands. The final review reads `ongoing` and `in_review` as claimed by another session (`SKILL.md`, Final Review). `blocked` is written by `scripts/red_flag.py`, which marks and clears it and never touches `ongoing` or `in_review` (the two coexist: blocking is not unclaiming); read by `scripts/enter.py`, whose `<issue>` refuses a blocked issue and whose `--next` passes over one; and read by the final review as `[BLOCKED_LABEL]` (`SKILL.md`, Final Review), which routes a sweepable finding on a blocked issue `ask`. | The label half of `enter` is skipped and says so; so is each label step of `race grid` and `race finish`, which never blocks either. With `blocked` unset, `red_flag.py` refuses and nothing counts as blocked: `enter`, `enter --next` and the final review behave as if no issue were. Never a guessed name: `gh issue edit --add-label` rejects a label the project has not already created, which aborts `enter` before any comment is posted — a safer failure than the label being silently created out of a guess. |
| `branch_prefix` | The prefix a cut branch carries ahead of `<issue>-<slug>`, separator included (`feat/`, `race/`). | Yes for `enter`. | `scripts/tracker.py`'s `branch_name()`, to cut one, and `resolve_branch()`, to find one that already exists. | `enter` refuses rather than cutting an unprefixed branch. |
| `claim_expiry_hours` | Hours a claim is honoured before another session may treat it as abandoned — **one half of the test**, never the whole of it. | Yes for `enter`'s refusal path. | `scripts/tracker.py`'s `is_stale()`, via `scripts/enter.py`. | `enter` cannot judge an existing claim and refuses. |
| `backlog_milestone` | The milestone holding not-yet-scheduled work. | Yes for `file`. | `scripts/file.py`'s `build_command()`. | `file` refuses rather than filing into the milestone in flight. |
| `notes_milestone` | The milestone holding **recorded decisions that are not work** — a ruling and its reasoning, kept so a later reader finds the answer rather than assuming an oversight. | Yes for `file --notes`; unused otherwise. | `scripts/file.py`'s `build_command()`. | `file --notes` refuses, naming the key. It never falls back to `backlog_milestone`: a note filed silently into the backlog is the defect the key exists to remove, and the fallback would be indistinguishable from not having the key at all. |
| `bounded_via_wdd` | Keeps its name from before the rename; `true` reads "through race". Whether a bounded change goes through race (`true`) or is implemented directly (`false`). | No. | `skills/race/scripts/project-config` (`--flag`), `skills/brainstorming/SKILL.md` (the bounded path). | Bounded implements directly; brainstorming warns when the project's own instructions require the race. |
| `runner` | The garage's per-repo settings: an object with two optional keys. `env` is an object of string variables set for every garage job of this repo; `setup` is a root command run once per runner, before that runner's first job for this repo, and again whenever its text changes. Only a machine running a garage this repo has joined reads it (see [`garage.md`](garage.md)). | No. | `garage/garage_config.py` (`parse_runner_section`, which checks it), `garage/garage_service.py` (fetches it from each joined repo's **default branch** through the GitHub API, never from a checkout, so a pull request cannot change a command that runs as root in a shared runner). | The repo still gets runners, with no extra variables and no setup. An `env` name that is not a plain variable name (letters, digits and `_`, not starting with a digit), or that starts with `ACTIONS_RUNNER_` or `PITCALL_`, or that is a name the recipe's host side reads (`PATH`, `HOME`, `LANG`, `LC_*`, `DOCKER_*` and the rest listed in `garage.md`), makes the repo "config invalid" in `garage status`, and it gets no runner until a fixed config reaches its default branch. |

**How to recognise the right value:**

- `outbound_allowlist` — never inferred, never proposed by `setup`. It is asked of a
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
- `status_labels` — the project's existing label taxonomy for claim states, if it
  has one (`gh label list`). Leave a sub-field unset rather than inventing a name
  the project does not already use.
- `branch_prefix` — look at how issue-linked branches are already named in this
  project's history (recent merged PR branch names) for a consistent shape.
- `claim_expiry_hours` — a convention this tool will own outright rather than a
  project fact to discover; pick a number that comfortably outlasts one working
  session.
- `backlog_milestone` — the project's milestone list (e.g. `gh api
  repos/<owner>/<repo>/milestones`) for the one nothing is currently scheduled
  against.
- `notes_milestone` — a project has one only if it has decided to separate
  *recorded rulings* from *deferred work*. Most have not, and absent is the
  correct value then. The test is what closing an issue there would mean: on the
  backlog it means the work got done, and in a notes milestone it means the
  reason the ruling rested on stopped being true. If a project cannot say which
  of those it wants, it does not need this key yet.
- `bounded_via_wdd` — look for a rule in `CLAUDE.md`, `AGENTS.md` or contributor
  docs requiring every change to running code to go through the race
  or a reviewed plan. Found → propose `true` and quote that line as the evidence.
  Propose `false` only when the project explicitly allows small changes directly.
  Otherwise leave it unset: `false` is a decision the project makes, not a default.
- `runner` — propose it only when the project's CI already runs on self-hosted runners (a
  `runs-on` naming `self-hosted` under `.github/workflows/`). `setup`: the system packages those
  jobs install for themselves today — an `apt-get install` step repeated in every self-hosted job
  is the clearest evidence. `env`: plain, non-secret variables every such job sets in its
  workflow's `env:`. Never a token or a password: those belong in GitHub secrets, and this file is
  tracked. Otherwise leave it unset — a repo with no `runner` section still gets runners.

## Declared, but nothing reads them yet

These keys exist in `pitcall.config.example.json` and are legitimate places to
record a value, but no shipped code consumes them today. Writing a value here has
no observable effect until the work that reads it lands; that work is specified
elsewhere, not by this file. Unlike `worktree_dir` and `plan_dir` above, nothing
here is standing in for these — there is simply no consumer yet.

| Key | Intended meaning | Required? | What reads it | If absent |
| --- | --- | --- | --- | --- |
| `in_lap` | Shell command line to tear the stack `out_lap` started back down. | No. | Nothing shipped. `scripts/lane_config.py` only carries the value through (see the compatibility note above); it appears otherwise in this project's own test fixtures (`tests/test_pit.py`, `tests/test_lane_config.py`), which are not reads of this key. | No effect either way. Do not write it expecting a step to run. |

**How to recognise the right value, if you are choosing one ahead of the reader that
will use it:**

- `in_lap` — mirror `out_lap`: the command that reverses it (`docker compose
  down`, stopping whatever `out_lap` started).

## Not a key of this plugin

`premerge_script` is not documented above because it does not exist here: except
for this paragraph naming it, it appears nowhere in this repository — not in code,
not in `pitcall.config.example.json`. It was invented against a surface that had
never documented what it actually was. If you find it in a config you're adopting,
it does nothing when read by this plugin; the project that wrote it meant
something this plugin does not implement.
