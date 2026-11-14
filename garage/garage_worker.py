"""One runner slot's lifecycle: start, set up, register, run one job, clean.

The service owns the loop and the threads; this module owns what happens to one slot. Every
change to a Slot happens under its lock, and recipe and GitHub calls happen outside it, so the
service's loop never waits behind a container start or a job.

GitHub calls catch OSError as well as ApiError: a network failure surfaces from urllib as an
OSError, and one escaping a worker thread would leave its slot stuck.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field

import garage_decide
import garage_github
import garage_recipe

JIT_VARIABLE = "ACTIONS_RUNNER_INPUT_JITCONFIG"

#: What a slot forgets when it is handed back: nothing assigned, nothing registered.
FREE_FIELDS = {"repo": None, "labels": None, "registration_id": None, "registration_name": None,
               "job": None, "proc": None, "seen_busy": False, "ended_by_garage": False}


def _stderr(message: str) -> None:
    print(f"garage: {message}", file=sys.stderr, flush=True)


def _last_line(output: str) -> str:
    lines = output.strip().splitlines()
    return lines[-1] if lines else "no output"


@dataclass
class Slot:
    name: str
    state: str = "stopped"
    repo: str | None = None
    labels: list[str] | None = None
    registration_id: int | None = None
    registration_name: str | None = None
    job: str | None = None
    since: float = 0.0
    failures: int = 0
    retry_at: float | None = None
    error: str | None = None
    container_running: bool = False
    needs_cleanup: bool = False
    seen_busy: bool = False
    ended_by_garage: bool = False
    proc: object | None = None
    count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def to_state(self) -> dict:
        return {"state": self.state, "repo": self.repo, "labels": self.labels,
                "registration": self.registration_name, "job": self.job, "error": self.error,
                "retry_at": self.retry_at, "since": self.since}


@dataclass
class Outcome:
    kind: str
    detail: str = ""
    api_error: garage_github.ApiError | None = None


class Worker:
    def __init__(self, garage_id: str, api, recipe, clock=time.time, log=_stderr,
                 killpg=os.killpg):
        self.garage_id, self.api, self.recipe, self.clock, self.log, self.killpg = (
            garage_id, api, recipe, clock, log, killpg)

    # --- slot changes, always under the slot's lock --------------------------------------------

    def _update(self, slot: Slot, **fields) -> None:
        with slot.lock:
            if "state" in fields:
                slot.since = self.clock()
            for name, value in fields.items():
                setattr(slot, name, value)

    def _free(self, slot: Slot) -> None:
        with slot.lock:
            slot.state = "idle" if slot.container_running else "stopped"
            slot.since = self.clock()
            for name, value in FREE_FIELDS.items():
                setattr(slot, name, value)

    def _broken(self, slot: Slot, error: str, **fields) -> Outcome:
        now = self.clock()
        with slot.lock:
            slot.failures += 1
            slot.state, slot.since, slot.error = "broken", now, error
            slot.retry_at = now + garage_decide.backoff_seconds(slot.failures)
            for name, value in fields.items():
                setattr(slot, name, value)
        self.log(f"{slot.name}: broken — {error}")
        return Outcome("broken", error)

    def _ended(self, slot: Slot) -> bool:
        with slot.lock:
            return slot.ended_by_garage

    # --- GitHub, never raising ------------------------------------------------------------------

    def _lookup(self, repo: str, runner_id: int) -> bool | None:
        """True: GitHub still lists the registration. False: it does not. None: it could not say."""
        try:
            return self.api.runner(repo, runner_id) is not None
        except (garage_github.ApiError, OSError) as exc:
            self.log(f"{repo}: cannot look up registration {runner_id}: {exc}")
            return None

    def _delete(self, repo: str, runner_id: int) -> bool | None:
        """True: gone. False: GitHub refused, the runner is busy. None: the call failed."""
        try:
            deleted = self.api.delete_runner(repo, runner_id)
        except (garage_github.ApiError, OSError) as exc:
            self.log(f"{repo}: cannot delete registration {runner_id}: {exc}")
            return None
        if not deleted:
            self.log(f"{repo}: GitHub kept registration {runner_id}: it is busy")
        return deleted

    # --- the lifecycle --------------------------------------------------------------------------

    def run_job(self, slot: Slot, repo: str, labels: list[str], section) -> Outcome:
        if not slot.container_running:
            self._update(slot, state="starting")
            result = self.recipe.run("start", slot.name, repo)
            if result.returncode != 0:
                return self._broken(
                    slot, f"start exited {result.returncode}: {_last_line(result.output)}",
                    **FREE_FIELDS)
            self._update(slot, container_running=True)
        if section.setup:
            self._update(slot, state="setup")
            result = self.recipe.run(
                "exec", slot.name, repo, args=("setup",),
                extra_env={"PITCALL_SETUP": garage_recipe.setup_script(repo, section.setup)})
            if result.returncode != 0:
                self._free(slot)
                return Outcome("setup failed", result.output)
        if self._ended(slot):  # the garage is stopping: register nothing
            self._free(slot)
            return Outcome("ended")
        with slot.lock:
            slot.count += 1
            name = f"pitcall-{self.garage_id}-{slot.name}-{slot.count}"
        try:
            runner_id, jit = self.api.jit_config(repo, name, labels)
        except (garage_github.ApiError, OSError) as exc:
            self._free(slot)
            api_error = exc if isinstance(exc, garage_github.ApiError) else None
            return Outcome("api error", str(exc), api_error=api_error)
        try:
            proc = self.recipe.spawn_job(slot.name, repo, {**section.env, JIT_VARIABLE: jit})
        except OSError as exc:
            self._delete(repo, runner_id)  # no runner process will ever use it
            return self._broken(slot, f"exec job could not start: {exc}", **FREE_FIELDS)
        with slot.lock:
            slot.state, slot.since = "waiting", self.clock()
            slot.registration_id, slot.registration_name, slot.proc = runner_id, name, proc
            ended = slot.ended_by_garage
        self.log(f"{slot.name}: registered {name} for {repo}")
        if ended:  # the garage began stopping while this registration was being made
            self._delete(repo, runner_id)
            self._end_job(proc)
        code = proc.wait()
        return self._after_job(slot, repo, runner_id, code)

    def _end_job(self, proc) -> None:
        """SIGTERM the job's whole process group: spawn_job gave it its own session."""
        try:
            self.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # the job already exited

    def _after_job(self, slot: Slot, repo: str, runner_id: int, code: int) -> Outcome:
        with slot.lock:
            slot.state, slot.since = "cleaning", self.clock()
            seen_busy, ended = slot.seen_busy, slot.ended_by_garage
        registered = self._lookup(repo, runner_id)
        result = self.recipe.run("cleanup", slot.name, repo)
        if registered is not False:
            self._delete(repo, runner_id)
        if result.returncode != 0:
            return self._broken(
                slot, f"cleanup exited {result.returncode}: {_last_line(result.output)}",
                needs_cleanup=True, **FREE_FIELDS)
        if not ended and not seen_busy and registered:
            return self._broken(
                slot, f"exec job exited {code} before its runner picked up a job", **FREE_FIELDS)
        self._free(slot)
        if ended:
            return Outcome("ended")
        self._update(slot, failures=0)
        return Outcome("done", f"exit {code}")

    def check_pickup(self, slot: Slot, now: float, deadline_seconds: float) -> None:
        with slot.lock:
            if slot.state != "waiting":
                return
            repo, runner_id, since = slot.repo, slot.registration_id, slot.since
        try:
            info = self.api.runner(repo, runner_id)
        except (garage_github.ApiError, OSError) as exc:
            self.log(f"{repo}: cannot check registration {runner_id}: {exc}")
            return
        if info is None or info.get("busy"):  # None: a one-job runner is gone once a job took it
            self._mark_busy(slot, runner_id)
            return
        if now - since < deadline_seconds:
            return
        deleted = self._delete(repo, runner_id)  # first: GitHub refuses if a job just arrived
        if deleted is False:
            self._mark_busy(slot, runner_id)
            return
        if deleted is None:
            return
        with slot.lock:
            if slot.state != "waiting" or slot.registration_id != runner_id:
                return
            slot.ended_by_garage = True
            proc = slot.proc
        self.log(f"{slot.name}: no job picked up its registration for {repo} in time; ended it")
        self._end_job(proc)

    def _mark_busy(self, slot: Slot, runner_id: int) -> None:
        with slot.lock:
            if slot.state == "waiting" and slot.registration_id == runner_id:
                slot.state, slot.since, slot.seen_busy = "busy", self.clock(), True

    def stop_idle(self, slot: Slot, now: float, idle_seconds: float) -> bool:
        """Called on the service's loop thread, the only thread that hands slots out."""
        with slot.lock:
            if slot.state != "idle" or not slot.container_running or now - slot.since < idle_seconds:
                return False
        result = self.recipe.run("stop", slot.name)
        if result.returncode != 0:
            self._broken(slot, f"stop exited {result.returncode}: {_last_line(result.output)}")
            return False
        self._update(slot, state="stopped", container_running=False)
        return True

    def retry(self, slot: Slot, now: float) -> bool:
        """Clean and stop a broken runner once its backoff has passed, so it restarts known-clean."""
        with slot.lock:
            if slot.state != "broken" or slot.retry_at is None or now < slot.retry_at:
                return False
            slot.retry_at = None  # claimed: the loop does not start a second retry of this slot
        for command in ("cleanup", "stop"):
            result = self.recipe.run(command, slot.name)
            if result.returncode != 0:
                self._broken(
                    slot, f"{command} exited {result.returncode}: {_last_line(result.output)}",
                    needs_cleanup=command == "cleanup")
                return False
        self._update(slot, state="stopped", container_running=False, needs_cleanup=False,
                     error=None)
        return True

    def end(self, slot: Slot) -> None:
        """Shutdown: delete a registration no job has picked up, and end a running job."""
        with slot.lock:
            slot.ended_by_garage = True
            state, repo, runner_id, proc = slot.state, slot.repo, slot.registration_id, slot.proc
        if state == "waiting":
            self._delete(repo, runner_id)
        if proc is not None and state in ("waiting", "busy"):
            self._end_job(proc)
