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


def _cmd_run(session: str, worktree: str) -> int:
    # The config comes from the checkout the bring-up will run in — one answer to
    # "which checkout", not two that can disagree.
    cfg = load_config(worktree)
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
