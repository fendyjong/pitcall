"""Project facts the lane needs, and where to find them.

The config is committed rather than held in a secret store: widening
`outbound_allowlist` is a safety change and must appear in a diff.

This module ships in a plugin that is installed outside the project it
configures, so its own location says nothing about which project a session is
working in — only the caller's location does. `shared_root()` and
`worktree_root()` are that resolution, and it deliberately happens nowhere
else.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

CONFIG_NAME = "pitcall.config.json"          # legacy location, repo root
CONFIG_DIR = ".pitcall"
CONFIG_BASENAME = "config.json"

#: Keys the workflow reads and cannot guess. Most are consumed by the skills
#: rather than by this module; the check lives here because this is the one
#: place the config is parsed, and a project is configured once rather than
#: discovering a hole per command.
#:
#: `outbound_allowlist` is deliberately NOT here. Only `allowlisted()` reads it,
#: and it already fails closed with a KeyError naming the key, whereas a project
#: that sends nothing outbound has no such list to write and should not be made
#: to invent one.
REQUIRED_KEYS = ("bringup", "validate", "default_branch", "required_check")


def _git(*args: str) -> str:
    """Run git and return stripped stdout, or "" when the command failed.

    A non-zero exit is an ANSWER here ("not a repository", "not a submodule"),
    not an error to raise on — every caller below distinguishes the two by what
    it does with the empty string.
    """
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def _enclosing_project_of_git_dir(common: Path) -> Path | None:
    """The project whose git dir CONTAINS this one, or None if it is top level.

    Git keeps a submodule's real git directory inside the superproject's:
    `<super>/.git/modules/<path>`, or `<super>/.git/worktrees/<wt>/modules/<path>`
    when the superproject is itself checked out into a linked worktree. When the
    caller is in a LINKED WORKTREE of a submodule this is the ONLY handle left,
    because `--show-superproject-working-tree` answers from the gitlink path and
    a linked worktree is not that path — it returns nothing, and the climb would
    stop inside the submodule.

    The test is a `.git` component that is not the LAST one. `<project>/.git` is
    a project's own git dir and settles; anything nested below a `.git` belongs
    to the repository that owns that `.git`. Matching the outermost one settles
    the deepest nesting in a single step.
    """
    parts = common.parts
    for i, part in enumerate(parts[:-1]):
        if part == ".git" and i > 0:
            return Path(*parts[:i])
    return None


class _Settled(NamedTuple):
    """Where the climb stopped, and whether it had to leave a git dir to get there.

    `left_a_git_dir` is the whole reason this is a pair rather than a path. The
    two roots need DIFFERENT AMOUNTS OF CLIMBING, which is the same lesson as
    "two questions that look like one", one level further down: leaving a
    submodule's own git dir is required to answer *which lane*, and is exactly
    the case where *which checkout* has no defensible answer at all.
    """

    directory: Path
    left_a_git_dir: bool


def _climb_to_the_project(start: Path | None = None) -> _Settled:
    """Walk up out of any submodule — or submodule worktree — to the project.

    A submodule is its own repository, so asking git from inside one answers
    about *it*. A session working in a submodule is working on the project that
    contains it and must queue behind the sessions working elsewhere in that
    project. Climbing is what replaces the module pin this code used to rely on:
    the pin answered "which repository is this code in", and the question after
    the move into a plugin is "which project is this session working on".

    TWO ways up, and both are needed. `--show-superproject-working-tree` handles
    a submodule in its normal place; the git-dir nesting handles a linked
    worktree OF a submodule, where git returns nothing and the naive answer is a
    lane inside the superproject's git dir that the project itself never
    resolves to. With two submodules under one directory the naive answer is
    worse than wrong: worktrees of both collapse onto ONE lane they share with
    each other and with nothing else.

    KNOWN AND UNFIXED: `GIT_DIR` / `GIT_WORK_TREE` in the environment override
    `-C` and silently redirect every answer below to the same wrong place. It is
    not introduced here — the pinned version had it too — and it is recorded
    rather than handled so the next reader does not rediscover it as a surprise.
    """
    d = Path(start or Path.cwd()).resolve()
    seen: set[Path] = set()
    left_a_git_dir = False
    while True:
        if d in seen:
            # Unreachable unless git reports a cycle. Loud, because the
            # alternative is a session waiting forever on a lane that never
            # resolves — and a hang is the one failure this module must not have.
            raise RuntimeError(f"lane: cycle while resolving the project from {d}")
        seen.add(d)
        superproject = _git("-C", str(d), "rev-parse", "--show-superproject-working-tree")
        if superproject:
            d = Path(superproject)
            continue
        outer = _enclosing_project_of_git_dir(Path(_rev_parse(
            d, "--path-format=absolute", "--git-common-dir")))
        if outer is None:
            return _Settled(d, left_a_git_dir)
        # Only a submodule's git dir nests inside another repository's, so this
        # step fires exactly when the caller stands in a WORKTREE of a
        # submodule — the shape where "which checkout" stops having an answer.
        left_a_git_dir = True
        d = outer


def _rev_parse(d: Path, *args: str) -> str:
    """`git rev-parse` from `d`, refusing to answer when there is no repository.

    Fail loudly. Falling back to cwd would give every caller a root of its own —
    indistinguishable from a working one, and excluding nobody.
    """
    out = _git("-C", str(d), "rev-parse", *args)
    if not out:
        raise RuntimeError(
            f"lane: {d} is not inside a git repository — cannot resolve a project"
        )
    return out


# There are TWO roots below, and in the main checkout they are the SAME PATH —
# which is exactly why one function answering both questions got as far as
# review. They diverge only in a linked worktree, which is the case this module
# exists for.


def shared_root(start: Path | None = None) -> Path:
    """Where the LOCK lives: the one root every worktree of a project agrees on.

    The parent of the shared git dir, so a project checked out into five
    worktrees still has one lane. Exclusion has to be machine-wide: a
    worktree-scoped answer hands each session a lane of its own, and then two
    sessions both hold "the lane" and both bring the stack up — the interleave
    with no error that this module exists to prevent.

    Unaffected by the ambiguity `worktree_root()` refuses on: "which lane" has
    a defensible answer in every shape, including a worktree of a submodule.
    """
    settled = _climb_to_the_project(start)
    return Path(_rev_parse(
        settled.directory, "--path-format=absolute", "--git-common-dir")).parent


def worktree_root(start: Path | None = None) -> Path:
    """Where the WORK is: the checkout the caller is actually standing in.

    What `bringup` runs against, and where the config is read from. Validation
    is run *from a worktree*, so defaulting to the main checkout would test code
    the session is not working on — silently, and with a plausible-looking
    result.

    The config is read from here deliberately. It is committed, so each branch
    carries its own copy: a branch that changes its validate command is tested
    with that command, and a branch that widens the outbound allowlist does so
    visibly in its own diff. Reading it from the main checkout would test a
    branch against a config nobody on that branch wrote.

    **Refuses rather than guesses.** In a worktree of a submodule there is no
    defensible answer: the session is working on the submodule, the bring-up
    belongs to the project, and picking either silently would run `validate`
    against a checkout the session never wrote and report green on it. That is
    the failure this module keeps producing — a loud failure quietly becoming a
    wrong answer — so the ambiguity is raised, not resolved. The caller settles
    it by passing `--worktree`, which is taken at face value.
    """
    settled = _climb_to_the_project(start)
    if settled.left_a_git_dir:
        raise RuntimeError(
            f"lane: {Path(start or Path.cwd()).resolve()} is a worktree of a "
            f"submodule of {settled.directory} — there is no unambiguous checkout "
            f"to run the project's bring-up in. Pass --worktree explicitly."
        )
    return Path(_rev_parse(settled.directory, "--show-toplevel"))


def _resolve_config_in(root: Path) -> Path:
    """Locate the config within one checkout root.

    Both locations present is refused rather than ordered: a precedence rule
    lets two configs disagree in silence, and the one that loses is invisible.
    """
    new = root / CONFIG_DIR / CONFIG_BASENAME
    legacy = root / CONFIG_NAME
    if new.exists() and legacy.exists():
        raise RuntimeError(
            f"lane: two configs in {root} — {CONFIG_DIR}/{CONFIG_BASENAME} and "
            f"{CONFIG_NAME}. Delete the legacy one; having both means a silent "
            f"disagreement about which is live."
        )
    if new.exists():
        return new
    if legacy.exists():
        sys.stderr.write(
            f"lane: {CONFIG_NAME} is deprecated — move it to "
            f"{CONFIG_DIR}/{CONFIG_BASENAME} (and un-ignore that path).\n"
        )
        return legacy
    return new


def _checkout_root(checkout: Path | str | None) -> Path:
    """The checkout `config_path()` and `load_config()` both resolve against.

    Shared so the two never diverge on what "the checkout" means — including
    when one of them reports it in an error message.
    """
    return Path(checkout) if checkout else worktree_root()


def config_path(checkout: Path | str | None = None) -> Path:
    """The config in `checkout`, or in the caller's own — never the shared root.

    An explicit `checkout` is taken at face value and not re-resolved: it is how
    a caller answers the question `worktree_root()` refuses to guess at, and
    re-resolving it would raise the very error the flag exists to settle.

    Threading it also keeps ONE answer to "which checkout": the config is read
    from the same directory the bring-up runs in. They could previously differ —
    `lane run --worktree X` ran in X while reading the config from cwd's
    checkout — which is the same class of quiet mismatch as everything else here.
    """
    return _resolve_config_in(_checkout_root(checkout))


def load_config(checkout: Path | str | None = None) -> dict:
    """Read the project's config, refusing an incomplete one.

    **A missing key is a loud failure, never a default.** The plugin is generic:
    it cannot know that a project's default branch is `master` or that its
    required check is called `ci`. A silent default would cut a branch from the
    wrong base or gate a merge on a check that does not exist — both of which
    look exactly like working behaviour until the moment they do not.

    PRESENCE is what is checked, never truthiness: a key written as `null` is a
    project saying "there is no such step", and the lane already skips a step
    configured that way. Treating that as missing would erase the distinction
    the check exists to draw.
    """
    root = _checkout_root(checkout)
    path = _resolve_config_in(root)
    try:
        cfg = json.loads(path.read_text())
    except FileNotFoundError:
        # Name the checkout we resolved, not just the path we failed to open:
        # since the resolution follows the caller's cwd, "the wrong checkout" is
        # now a reachable mistake and the path alone does not show it. `root` is
        # the checkout itself; `path.parent` is NOT — once neither location
        # exists it is `.pitcall/`, a directory inside the checkout, and naming
        # it here would be exactly the mistake this comment describes.
        raise RuntimeError(
            f"lane: no {CONFIG_DIR}/{CONFIG_BASENAME} or {CONFIG_NAME} in {root} "
            f"— that is the checkout resolved from the current directory"
        ) from None
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    if missing:
        # Every missing key at once: one per run means one edit, one re-run and
        # one more failure.
        raise RuntimeError(
            f"{path}: missing required key(s): {', '.join(missing)} — "
            f"the plugin is generic and cannot guess them"
        )
    return cfg


def allowlisted(phone: str) -> bool:
    """Exact string match only.

    Deliberately does not normalise. A '+' prefix or stray space is a different
    number, and treating it as the same is how a near-miss reaches a real person.
    """
    return phone in set(load_config()["outbound_allowlist"])
