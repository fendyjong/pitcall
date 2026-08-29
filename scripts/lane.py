"""Validation lane: one bring-up at a time, machine-wide.

Several sessions share one machine, each working in its own worktree, and only
one local stack can exist — the containers share a project name and bind fixed
host ports. Two simultaneous bring-ups interleave, and each session concludes
the other's stack is broken **with nothing erroring**. This module serialises
them: one holder at a time, a queue, and a hand-off.

Two roots, and they are not the same question. The exclusion only works if
every caller resolves to the SAME lock file, so the LANE is scoped to the
project's shared git dir (`lane_config.shared_root()`) and never to the invoking
worktree. What gets brought up and validated is the CALLER's checkout
(`lane_config.worktree_root()`), because validation runs from a worktree and a
session must test the code it is actually working on — and where no unambiguous
checkout exists, that function refuses rather than picking one, because a
bring-up run against a checkout the session never wrote reports green on it. In the main checkout the
two are the same path, which is why collapsing them is easy and why a test pins
their divergence.

Both follow the CALLER, because this module ships in a plugin installed outside
the project, and both climb out of submodules, because a call made from inside
one would otherwise resolve to the submodule's own git dir and silently yield a
second, private lane.

## Liveness is a held kernel lock, never a recorded pid

The holder keeps `LOCK_EX` on `lane.lock` for its whole run; a would-be acquirer
probes `LOCK_EX | LOCK_NB` and reads success as "the previous holder's process is
gone". A pid cannot do this job here: a session invokes Python as a short-lived
subprocess, so a recorded `os.getpid()` is dead the instant it is written and the
next acquirer would reclaim a lane whose bring-up is still running — while a
recycled pid wedges the lane forever, and there is deliberately no timeout to
break it. The kernel releases a flock at process teardown even on `SIGKILL`, and
a kernel lock cannot be recycled, so reclaim stays exact: no timeout, only a
genuinely departed holder. The pid in the lock file is display only — **no
decision anywhere keys on it** — but it is not dead weight: `status()` prints it,
and that print is the only handle the recovery below has. Do not clean it away
as unused.

The consequence: **holding the lane needs a process that outlives the
acquisition.** `acquire()` therefore returns a `Held` whose kernel lock lives
exactly as long as the object's file handle, and `lane run` (see `main`) is the
entry point that holds it across `bringup` and `validate` in one process.

## The receipt: what this run validated

A successful `validate` leaves `.pitcall/receipts/<sha>.json`, naming the commit
it covered. A later step merges a pull request once its checks are green, and
what makes that safe is a rule that nothing merges without having passed a lane
run — so the receipt turns that rule from something a session remembers into
something a machine can check.

It is keyed by SHA, never by branch: the ordinary failure is a review fix landing
after validation and inheriting its approval, and a branch-keyed receipt says
"this branch was fine" long after it stopped being the branch that was tested.

**A receipt that attests to nothing is worse than no receipt**, because it reads
exactly like one that does. Four ways to write one, all of which this codebase
has shipped or nearly shipped: for a step that was SKIPPED (`validate` may be
null, and the lane skips a step configured that way — the run still exits 0),
for a step that FAILED, for the WRONG COMMIT (the sha is resolved from the
validated worktree, never the caller's cwd; `lane run --worktree X` means those
two are routinely different directories), and for a commit that arrived DURING
the run. See `_cmd_run`.

That last one is the subtle one, and it is the failure this whole file exists to
prevent arriving through the one window the lock does not close. **The lock stops
another SESSION interfering; it says nothing about the holder's own worktree
changing underneath it.** Commit while `validate` is running and a sha read after
`validate` returns names a commit that was never validated — approval inherited
by a change nothing checked, which is precisely the thing being defended against.
So the sha is captured BEFORE `bringup` and re-read after `validate`, and a run
whose HEAD moved writes NO receipt: `validate` observed a state that is neither
commit, so neither is an honest answer, and there is nothing to fall back to.

## A commit is a state, not a name

**The tree has to match HEAD, or the sha is a label on the wrong thing.** Edit a
tracked file without committing and HEAD never moves at all, so every check above
passes while `validate` runs against a state no commit contains — and the receipt
then certifies a commit that `validate` was never given. Same dishonesty as the
moving head, reached without anything moving. So the capture reads the worktree's
DIRT as well as its sha, and a run that began with tracked modifications writes no
receipt either.

## What none of that catches

Written down because silence here would read as coverage, and because each of
these is cheap to mistake for a bug later:

- **A tracked file changed BY the run.** The tree is only inspected at capture,
  so a `bringup` or `validate` that rewrites a tracked file (a regenerated
  lockfile, a snapshot) still gets a receipt. Deliberate: that dirt is a
  CONSEQUENCE of validating, not an input to it, and refusing on it would make
  the lane unusable for any project whose test run touches tracked state.
- **HEAD moving and moving back** to the same sha inside one run. The comparison
  sees equality and writes. Catching it needs a reflog watcher, which is a great
  deal of machinery for a case that requires someone to commit and then reset
  mid-validate.
- **A submodule passed as `--worktree`.** The receipt is keyed on the
  submodule's sha and filed in the superproject's lane, where the superproject's
  merge step will not find a sha it recognises. Fail-closed for the parent, so
  it is noise rather than a hole.

## One project per run

The lane is resolved from the caller's cwd and the checkout from `--worktree`,
which is what lets a session in the main checkout validate a linked worktree.
Nothing stopped that pair naming two DIFFERENT PROJECTS — holding project A's
lane while validating project B, and filing the receipt in A where B's merge step
will never look. Inherited from the lock, which has the same shape and for which
it is arguably correct (the lane serialises per project). It is not a mode, it is
a mistake, and `_cmd_run` refuses it before taking the lane.

## Clearing a stuck lane

That leaves the owner one job: a live holder that is stuck. Clear it by **killing
the holding process** — `lane status` prints its pid, which is what that display
is for — and the kernel then drops the lock. Do NOT delete `lane.lock` by hand:
the holder's file handle keeps the lock on the now-unlinked inode while the next
acquirer creates and locks a fresh file, which is two holders at once — the one
thing this module exists to prevent.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from lane_config import load_config, shared_root, worktree_root

LOCK = "lane.lock"
QUEUE = "lane.queue.json"
GUARD = "lane.guard"

#: `lane run` found the lane held and queued the caller instead of running it.
#: Distinct from 1 (a step failed) so a caller can tell "not my turn" from "it broke".
EXIT_QUEUED = 75

#: `lane run` was re-issued while this same session's own earlier run still holds
#: the lane. Deliberately NOT EXIT_QUEUED: a queued caller has a place in the queue
#: and gets pushed when the holder releases, while this one has neither — it *is*
#: the holder, and nobody will ever push it. Collapsing the two would tell a session
#: to wait for a wake-up that cannot come.
EXIT_ALREADY_RUNNING = 76

#: `lane run --worktree X` was issued from inside a DIFFERENT project, so the
#: lane and the checkout name two projects. Not a failure of the run — the run
#: never starts. 64 is the conventional status for "you invoked this wrongly"
#: (EX_USAGE), which is what this is; it is deliberately not 1, so a caller
#: cannot mistake it for a step that failed.
EXIT_WRONG_PROJECT = 64


def lane_dir() -> Path:
    """The one directory every caller working on this project must agree on.

    The SHARED root, never the caller's worktree: five worktrees of a project
    must land on one lane or there is no exclusion. `shared_root()` raises
    rather than falling back when there is no project above the caller, and that
    stance is the point — a lane created in whatever directory the caller
    happened to be standing in is indistinguishable from a working one and
    excludes nobody.
    """
    d = shared_root() / ".pitcall"
    d.mkdir(exist_ok=True)
    return d


def receipts_dir() -> Path:
    """Where a validated commit is recorded, beside the lane it was run in.

    Under `lane_dir()`, so every worktree of the project reads and writes ONE
    set of receipts — the same reason the lock lives there. A worktree-scoped
    directory would let the merge step consult a set of receipts that the
    session which actually ran the lane never wrote to, and find nothing, and be
    unable to tell that from a branch that was never validated.
    """
    d = lane_dir() / "receipts"
    d.mkdir(exist_ok=True)
    return d


@contextmanager
def _guard(d: Path):
    """Serialise every mutation of the lock and the queue.

    Advisory, but every mutator in this module takes it, which is what makes
    check-then-act sequences below safe. `status()` is the one deliberate
    exception — see its docstring.
    """
    gp = d / GUARD
    gp.touch(exist_ok=True)
    with open(gp, "r+") as gh:
        fcntl.flock(gh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(gh, fcntl.LOCK_UN)


def _read(p: Path):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(p: Path, obj) -> None:
    """Write via temp + os.replace so a reader never sees a half-written file."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, p)


def _enqueue_locked(d: Path, session: str, worktree: str) -> None:
    """Caller must hold the guard."""
    q = _read(d / QUEUE) or []
    if any(e["session"] == session for e in q):
        return                                     # idempotent: re-registering keeps its place
    q.append({"session": session, "worktree": worktree, "queued_at": time.time()})
    _write_json(d / QUEUE, q)


class Held:
    """The lane, held. The kernel lock lives exactly as long as `_fh`.

    Use it as a context manager so the lock cannot outlive the block that owns
    it. The hand-off target is on `next_session` once `release()` has run, which
    is why the object is worth keeping after the block exits.

    The lock lives exactly as long as this object: drop the last reference and the
    handle closes, which frees the lane with nothing logged and nobody handed off.
    A caller that discards the return value of `acquire()` has not taken the lane,
    it has flickered it.
    """

    def __init__(self, d: Path, fh, session: str, worktree: str,
                 acquired_at: float, reentrant: bool) -> None:
        self._d = d
        self._fh = fh
        self.session = session
        self.worktree = worktree
        self.acquired_at = acquired_at
        #: True when this session already held the lane and we did NOT take the
        #: kernel lock a second time. Such a handle must never release.
        self.reentrant = reentrant
        self.next_session: str | None = None
        self._released = False

    def release(self) -> str | None:
        """Release the lane and return the next waiting session, or None."""
        if self.reentrant:
            # We never took the kernel lock; the outer acquisition of this same
            # session still holds it and its release does the hand-off. Releasing
            # here would free a lane out from under a live bring-up.
            return None
        if self._released:
            raise RuntimeError(f"lane already released by {self.session}")
        with _guard(self._d):
            lock = self._d / LOCK
            record = _read(lock)
            if record is None:
                # Not merely "someone else holds it" — nobody does. Falling through here
                # would pop a queue entry on behalf of a session that never held the lane,
                # double-advancing the queue past a waiter that is never woken.
                raise RuntimeError(f"lane is not held; {self.session} cannot release it")
            if record["session"] != self.session:
                raise RuntimeError(f"lane held by {record['session']}, not {self.session}")
            # Clear the record BEFORE dropping the kernel lock, both inside the guard:
            # a stale holder displayed over an already-free lock is the one state that
            # would make status() lie.
            lock.unlink(missing_ok=True)
            q = _read(self._d / QUEUE) or []
            nxt = q.pop(0)["session"] if q else None
            _write_json(self._d / QUEUE, q)
            self.next_session = nxt
            self._released = True
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            return nxt

    def __enter__(self) -> "Held":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._released:
            self.release()


def acquire(session: str, worktree: str) -> Held | None:
    """Take the lane, or register in the queue and return None.

    Returns a `Held` — a context manager keeping the kernel lock for its block.
    The lane is held for exactly as long as that object's process lives, which is
    the whole point: a bare call cannot hold a lane across its own exit, so it
    does not pretend to.
    """
    d = lane_dir()
    with _guard(d):
        lock = d / LOCK
        fh = os.fdopen(os.open(lock, os.O_RDWR | os.O_CREAT, 0o644), "r+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Someone's process is genuinely alive and holding it.
            fh.seek(0)
            try:
                record = json.loads(fh.read())
            except json.JSONDecodeError:
                record = None
            fh.close()
            if record and record.get("session") == session:
                # Our own earlier acquisition, still live — a retry after a transient
                # error, say. Enqueueing ourselves would mean waiting for a hand-off
                # that can only come from our own release, and there is no timeout
                # to break that. The queue is already idempotent on re-registration for
                # the same reason: re-entry by the same session is anticipated.
                return Held(d, None, session, worktree,
                            float(record.get("acquired_at", time.time())), reentrant=True)
            _enqueue_locked(d, session, worktree)
            return None
        # We hold the kernel lock, so whatever the file said was written by a holder
        # whose process is gone (or by nobody). Overwrite it — in place, not via the
        # temp-and-rename `_write_json` uses: a rename would put a NEW inode behind
        # the path, and the lock we just took is on this one. Every future acquirer
        # would then lock a different file and the exclusion would be gone.
        acquired_at = time.time()
        fh.seek(0)
        fh.truncate()
        json.dump(
            # pid is recorded for a human reading `lane status`, and that display is
            # the handle the "kill the stuck holder" recovery needs. No DECISION
            # keys on it — liveness is the flock — but it is read; see the module
            # docstring before deleting it as unused.
            {"session": session, "worktree": worktree,
             "pid": os.getpid(), "acquired_at": acquired_at}, fh
        )
        fh.flush()
        return Held(d, fh, session, worktree, acquired_at, reentrant=False)


def drop_and_next(session: str) -> str | None:
    """The pushed session was unreachable — drop it and return the one after.

    Without this the chain stalls silently: lane free, waiters waiting forever,
    and nothing anywhere reporting it.
    """
    d = lane_dir()
    with _guard(d):
        q = [e for e in (_read(d / QUEUE) or []) if e["session"] != session]
        _write_json(d / QUEUE, q)
        return q[0]["session"] if q else None


def status() -> dict:
    """Report holder, elapsed time and waiters.

    **Deliberately does NOT take the guard, and must not be "fixed" to.** This is
    the one thing that can be run against a lane whose holder is hung, and the
    guard is held across every mutation — so taking it here would make status()
    block behind the very holder it exists to diagnose.

    The cost is a torn read. The queue is written temp-then-rename and so is
    always whole, but the lock record is rewritten IN PLACE (it cannot be
    rename-swapped — the kernel lock is bound to that inode), so an unguarded
    read can catch it mid-write. `_read` turns that into None: this function
    reports "no holder" for the microseconds of one overwrite. It is a display
    glitch that corrects itself on the next call, and it can never be more than
    that, because nothing anywhere keys a decision on what status() returns.

    `holder` is the lock file's record: display only. The authoritative liveness
    signal is the kernel lock, which only `acquire()` probes — a record left by a
    crashed holder reads as a holder here, and is reclaimed silently by the next
    `acquire()`.
    """
    d = lane_dir()
    held = _read(d / LOCK)
    return {
        "holder": dict(held) if held else None,
        # Derived, never stored: two copies of "who holds" would drift.
        "elapsed_seconds": (time.time() - held["acquired_at"]) if held else None,
        "waiting": _read(d / QUEUE) or [],
    }


# --- lane run ------------------------------------------------------------
#
# The entry point that makes the lock mean anything: one process that acquires,
# runs bringup then validate, releases, and names the session to hand off to.


def _step(label: str, command: str, cwd: Path) -> int:
    # shell=True: the config's steps are shell command lines a project writes
    # ("make up", "npm run test:e2e"), and the config is committed — it is code
    # review's input, not a user's.
    print(f"[lane] {label}: {command}", flush=True)
    return subprocess.run(command, shell=True, cwd=cwd).returncode


def _worktree_sha(worktree: str) -> str | None:
    """HEAD of the checkout that was validated, or None if it has none.

    `-C <worktree>`, never the process's own cwd. `lane run --worktree X` runs
    the steps in X while the process stands wherever the caller invoked it, so a
    receipt built from cwd would name a commit nothing was run against — and the
    two roots are the same path in the main checkout, which is why that mistake
    survives casual testing. This module has resolved the wrong one of that pair
    twice already.
    """
    out = subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def _worktree_branch(worktree: str) -> str | None:
    """The branch checked out in `worktree`, or None when HEAD is detached.

    Recorded for a human reading a directory of receipts. **Nothing keys on it**
    — the receipt is filed under the sha precisely because a branch label
    outlives the commit it was attached to, which is the failure being defended
    against. `symbolic-ref` rather than `rev-parse --abbrev-ref`, which answers
    the literal string "HEAD" on a detached head and would record that as a
    branch name.
    """
    out = subprocess.run(["git", "-C", worktree, "symbolic-ref", "--short", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def _worktree_dirt(worktree: str) -> str | None:
    """Tracked changes absent from HEAD, or None when the tree matches it.

    **`-uno`: untracked files are excluded deliberately, and this is the line to
    read before widening it.** A scratch file, an editor backup, a build artifact
    nobody tracks — none of them is part of any commit's state, so none of them
    makes the receipt's claim false. Count them and the lane starts refusing on
    noise, and a guard that fires on noise is a guard someone switches off.

    Returns the porcelain text rather than a bool so the refusal can NAME what
    was dirty; a session told only "the tree is dirty" has to go and look. A
    checkout that is not a repository answers None here — clean — because the
    sha probe already refuses that case, and duplicating the refusal would mean
    two messages for one cause.
    """
    out = subprocess.run(["git", "-C", worktree, "status", "--porcelain", "-uno"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


class _Checkout(NamedTuple):
    """What the worktree was when the run began.

    A triple rather than a bare sha because "which commit" is not the whole
    question. The receipt claims `validate` ran against the STATE of a commit,
    and both of the other fields are ways that claim goes false while the sha
    stays put: `dirt` is the tree not matching HEAD, and `branch` is a label
    recorded at validation time rather than at write time, because a run can
    switch branches without moving.
    """

    sha: str | None
    branch: str | None
    dirt: str | None


def _capture(worktree: str) -> _Checkout:
    """Read the checkout's state. Called BEFORE the first step — see `_cmd_run`."""
    return _Checkout(sha=_worktree_sha(worktree),
                     branch=_worktree_branch(worktree),
                     dirt=_worktree_dirt(worktree))


def _project_of(worktree: str) -> Path | None:
    """The project a checkout belongs to, or None if it is in no repository.

    Probes first rather than catching `RuntimeError` around `shared_root()`:
    that would also swallow the cycle error `_climb_to_the_project` raises
    deliberately, and a hang is the one failure this module must not have.
    """
    probe = subprocess.run(["git", "-C", worktree, "rev-parse", "--git-dir"],
                           capture_output=True, text=True)
    return shared_root(Path(worktree)) if probe.returncode == 0 else None


def _record_receipt(session: str, worktree: str, before: _Checkout,
                    command: str) -> None:
    """Record the commit a SUCCESSFUL, UNDISTURBED `validate` covered.

    One caller, `_cmd_run`, which calls this only after `validate` ran and
    exited 0 and while it still holds the lane. Those two preconditions are the
    caller's to enforce — this function cannot tell a skipped step from a
    passing one and must never be given the chance to guess. The rest are
    enforced here, because each of them needs a value read before the steps
    began: `before` is that reading.
    """
    sha_after = _worktree_sha(worktree)
    if before.sha is None or sha_after is None:
        # Fail closed, and loudly. No receipt means the merge step refuses,
        # which is the safe direction; silence would leave the next session
        # hunting for a file that nothing ever wrote and no line ever mentioned.
        print(f"[lane] validate passed, but {worktree} has no resolvable HEAD — "
              "no receipt written")
        return
    if before.dirt is not None:
        # HEAD never moved, and the receipt would still be a lie: `validate` ran
        # against tracked content that is in no commit, so certifying `before.sha`
        # certifies a state `validate` was never given — and would have failed on,
        # in the case that matters. The same argument as the moved head, reached
        # without anything moving.
        print(f"[lane] {worktree} had uncommitted changes to tracked files "
              "when the run began:")
        for line in before.dirt.splitlines():
            print(f"[lane]   {line}")
        print(f"[lane] validate ran against a tree that is not {before.sha} — "
              "no receipt written. Commit or stash, then re-run.")
        return
    if before.sha != sha_after:
        # Neither sha is written, and that is deliberate. `validate` began at
        # one commit and ended at another, so it observed a state that is
        # neither: certifying the earlier one approves code that was replaced
        # mid-run, and certifying the later one approves code that was never
        # validated — the exact failure this file exists to prevent. There is no
        # honest fallback, so refuse and make the session re-run.
        #
        # Compared in FULL. Short prefixes collide, and two commits that differ
        # only past the seventh character would compare equal.
        print(f"[lane] HEAD moved during the run: {before.sha} -> {sha_after}")
        print("[lane] validate covered neither commit — no receipt written. "
              "Re-run the lane on a worktree nothing is committing to.")
        return
    path = receipts_dir() / f"{sha_after}.json"
    # Temp-then-rename, like the queue: a reader that catches a half-written
    # receipt must see no receipt (safe) rather than a truncated one, and a
    # partial file left in the directory is noise nobody can account for later.
    _write_json(path, {
        "sha": sha_after,
        # The branch as it was at VALIDATION time, not at write time. A run can
        # switch branches without moving the sha, and a field describing when
        # validation happened must not be read after the fact.
        "branch": before.branch,
        "session": session,
        "worktree": worktree,
        "validate_command": command,
        "validated_at": time.time(),
    })
    print(f"[lane] receipt: {path}")


def _cmd_run(session: str, worktree: str) -> int:
    # The config comes from the checkout the bring-up will run in — one answer to
    # "which checkout", not two that can disagree.
    cfg = load_config(worktree)

    # Refuse a run whose lane and checkout belong to different projects. Checked
    # BEFORE `acquire()`: taking project A's lane and then refusing would block
    # every other session in A for as long as it took to notice. None means the
    # checkout is in no repository at all — nothing to compare against, and such
    # a run already ends without a receipt because its sha cannot be resolved
    # either, so there is no incoherent state left to guard.
    project = _project_of(worktree)
    lane_here = lane_dir()
    if project is not None and project / ".pitcall" != lane_here:
        print(f"[lane] --worktree {worktree} is in project {project}")
        print(f"[lane] but this directory resolves the lane at {lane_here}")
        print("[lane] REFUSING: that would hold one project's lane while "
              "validating another, and file the receipt where nothing looks for it.")
        print("[lane] Run from inside the project you are validating.")
        return EXIT_WRONG_PROJECT

    held = acquire(session, worktree)
    if held is None:
        st = status()
        holder = st["holder"] or {}
        waiting = [e["session"] for e in st["waiting"]]
        print(f"[lane] held by {holder.get('session', '?')} "
              f"({holder.get('worktree', '?')}) for {st['elapsed_seconds'] or 0:.0f}s")
        print(f"[lane] queued as {session}; queue is {waiting}")
        print("[lane] STOP HERE. The holder pushes you a message when it releases.")
        return EXIT_QUEUED

    if held.reentrant:
        # `acquire()` recognising this session's own hold is a library property, not
        # a licence to run. Liveness is a kernel lock, so this branch can fire ONLY
        # while another LIVE process of this same session is mid-run — which makes
        # "continue anyway" precisely "start a second bring-up on top of a running
        # one", the interleaving this module exists to prevent. Reachable whenever a
        # `lane run` tool call times out while the bring-up is still going and the
        # caller re-issues it. Refuse, and do NOT release: the lane is held by that
        # other process's kernel lock and its own release does the hand-off.
        st = status()
        holder = st["holder"] or {}
        print(f"[lane] {session} is ALREADY running the lane in "
              f"{holder.get('worktree', '?')} — started {st['elapsed_seconds'] or 0:.0f}s ago")
        print("[lane] REFUSING to start a second bring-up on top of it.")
        print("[lane] STOP HERE. You are NOT queued: that run is yours and hands off "
              "by itself when it finishes.")
        return EXIT_ALREADY_RUNNING

    rc = 0
    validated = False
    # BEFORE the first step, not after the last one, and not between them.
    # `bringup` builds from the working tree too, so the whole run has to sit on
    # one commit for the receipt to mean anything; capturing after `validate`
    # would certify whatever HEAD happened to be by then, and capturing between
    # the two would miss a commit landing during the bring-up.
    before = _capture(worktree)
    if before.dirt is not None:
        # Said now as well as at the end: the run is still worth doing — a
        # session may well want to test a dirty tree — but it cannot produce
        # evidence, and learning that after a ten-minute bring-up is learning it
        # too late to act on.
        print(f"[lane] WARNING: {worktree} has uncommitted changes to tracked "
              "files; this run cannot produce a receipt")
    try:
        for label in ("bringup", "validate"):
            command = cfg.get(label)
            if not command:
                print(f"[lane] {label}: none configured, skipping")
                continue
            rc = _step(label, command, Path(worktree))
            if rc != 0:
                print(f"[lane] {label} FAILED (exit {rc})")
                break
            if label == "validate":
                # Set HERE and nowhere else. The obvious alternative — write the
                # receipt when the loop finishes with rc == 0 — is wrong twice
                # over: a run whose `validate` is null skips the step and still
                # leaves rc at 0 from the bringup, and a run with no steps at all
                # never touches rc. Both would be certified as validated.
                validated = True
        if validated:
            # Inside the try, so it happens while the lane is still held: nothing
            # else can be mid-bringup against this checkout as the sha is read.
            try:
                _record_receipt(session, worktree, before, cfg["validate"])
            except Exception as exc:
                # Deliberately broad, and deliberately not re-raised. `release()`
                # in the `finally` below has ALREADY popped the next waiter off
                # the queue by the time anything here could throw, so an escaping
                # exception skips the hand-off print — and that print is the only
                # thing that wakes that session. It would then wait forever, on a
                # free lane, with nothing anywhere reporting the loss. **A lost
                # receipt costs one re-run; a lost waiter costs a session.** The
                # failure is loud and fail-closed either way: no receipt means the
                # merge step refuses.
                print(f"[lane] receipt could not be written: {exc!r}")
                print("[lane] the run itself PASSED; re-run the lane to produce "
                      "the receipt the merge step needs.")
    finally:
        nxt = held.release()

    if nxt:
        print(f"[lane] released. HAND OFF TO: {nxt} — push it a message so it can acquire.")
        # An absolute path: this module lives in the plugin, not in the project
        # the reader is standing in.
        print(f"[lane] if that push fails: python3 {Path(__file__).resolve()} "
              f"drop --session {nxt}")
    else:
        print("[lane] released. No waiters.")
    return 0 if rc == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lane",
        description="Serialise stack bring-ups across every worktree on this machine.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run",
        help="acquire the lane, run bringup + validate, release, name the next session",
    )
    p_run.add_argument("--session", required=True, help="this session's addressable name")
    # Resolved after parsing, not as a default here: building the parser must
    # not require a project, or `lane --help` outside one dies before argparse
    # can answer.
    p_run.add_argument(
        "--worktree", default=None,
        help="the checkout to run in (default: the caller's own checkout; REQUIRED "
             "when standing in a worktree of a submodule, where there is no "
             "unambiguous one)")

    sub.add_parser("status", help="who holds the lane, for how long, and who is waiting")

    p_drop = sub.add_parser(
        "drop", help="drop an unreachable waiter and name the one after it")
    p_drop.add_argument("--session", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        # The caller's own checkout, NOT the shared root: defaulting to the
        # main checkout would run the bring-up against code this session is not
        # working on, and the result would look perfectly plausible.
        return _cmd_run(args.session, args.worktree or str(worktree_root()))
    if args.cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    nxt = drop_and_next(args.session)
    print(f"[lane] dropped {args.session}; next is {nxt}" if nxt
          else f"[lane] dropped {args.session}; queue is empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
