"""The garage service: poll the joined repos, hand runners out in turn, keep every runner clean.

`garage start` installs this directory to ~/.local/share/pitcall/garage/ and runs this file from
there under a systemd user unit or a launchd agent; everything it imports sits beside it
(decision 11). One loop thread polls and hands out; each runner it hands out runs its job on its
own thread (garage_worker), so polling carries on while containers start and jobs run.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

import garage_config
import garage_decide
import garage_github
import garage_paths
import garage_recipe
import garage_state
import garage_worker

ACCESS = (401, 403, 404)
PENDING = ("starting", "setup", "waiting")
FREE = ("idle", "stopped")
REPO_FIELDS = ("status", "reason", "queued", "labels", "retry_at")
APPLIED_AT_START = ("id", "runners", "recipe", "token_file")
SHUTDOWN_SECONDS = 240  # shutdown's grace for jobs to end, counted from the stop signal
PARK_SECONDS = 50  # with SHUTDOWN_SECONDS, inside the unit's 5-minute stop timeout
#: Reconcile waits this long for the runners' cleanup and stop: each command's own timeout, plus
#: the 10 s Recipe.run waits after killing one.
RECONCILE_SECONDS = 2 * (garage_recipe.TIMEOUT_SECONDS + 10)


def _stderr(message: str) -> None:
    print(f"garage: {message}", file=sys.stderr, flush=True)


def _thread(fn):
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


def _reason(exc: garage_github.ApiError) -> str:
    if exc.status in ACCESS:
        return f"{exc}: check the token's repository access and permissions"
    if exc.status >= 500:
        return f"GitHub error {exc.status}"
    return str(exc)


def _tail(output: str, lines: int = 3) -> str:
    return " / ".join(output.strip().splitlines()[-lines:]) or "no output"


class Garage:
    def __init__(self, config_path, cfg, api, recipe, state_path, clock=time.time, spawn=None,
                 log=None, version=""):
        self.config_path = Path(config_path)
        self.cfg = cfg
        self.api = api
        self.recipe = recipe
        self.state_path = Path(state_path)
        self.clock = clock
        self.spawn = spawn or _thread
        self.log = log or _stderr
        self.version = version
        # The recipe started each job's process group, so its killpg is the one that ends it.
        self.worker = garage_worker.Worker(cfg["id"], api, recipe, clock=clock, log=self.log,
                                           killpg=recipe.killpg)
        self.slots = {f"r{n}": garage_worker.Slot(f"r{n}") for n in range(1, cfg["runners"] + 1)}
        self.repo_status: dict[str, dict] = {}
        self.sections: dict[str, garage_config.RunnerSection] = {}
        self.cursor = 0
        self.config_error = None
        self.paused_until = None
        self.threads = []
        self._lock = threading.RLock()  # repo_status is written by the loop and by worker threads
        self._mtime = self._config_mtime()
        self._sync_repos()

    # --- threads --------------------------------------------------------------------------------

    def _start(self, fn) -> None:
        thread = self.spawn(fn)
        if thread is not None:
            self.threads.append(thread)

    def _join(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        for thread in self.threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self.threads = [t for t in self.threads if t.is_alive()]

    # --- the machine config ---------------------------------------------------------------------

    def _config_mtime(self):
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def _sync_repos(self) -> None:
        joined = self.cfg["repos"]
        with self._lock:
            for repo in [r for r in self.repo_status if r not in joined]:
                del self.repo_status[repo]
                self.sections.pop(repo, None)
            for repo, entry in joined.items():
                # Private (never in the state file): failures counts backoff rounds, cleared_by
                # says what ends a failing status ("poll": a clean read; "job": a job that runs),
                # probe names the one runner trying the repo again once its wait is over.
                status = self.repo_status.setdefault(repo, {
                    "status": "ok", "reason": None, "queued": 0, "labels": [], "retry_at": None,
                    "failures": 0, "cleared_by": None, "probe": None})
                status["labels"] = entry["labels"]

    def reload_config(self) -> None:
        mtime = self._config_mtime()
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            new = garage_config.parse_machine_config(self.config_path.read_text())
        except (OSError, ValueError, garage_config.ConfigError) as exc:
            self.config_error = str(exc)
            self.log(f"{self.config_path}: not applied, keeping the last config read: {exc}")
            return
        for key in APPLIED_AT_START:
            if new.get(key) != self.cfg.get(key):
                self.log(f"config: a change to {key} applies at the next garage start")
                new[key] = self.cfg[key]
        self.cfg, self.config_error = new, None
        self._sync_repos()

    # --- repos ----------------------------------------------------------------------------------

    def _back_off(self, repo: str, failing: str, reason: str, cleared_by: str) -> None:
        """One failure per backoff round: one that lands while the repo already waits joins it."""
        now = self.clock()
        with self._lock:
            status = self.repo_status.get(repo)
            if status is None or (status["status"] == failing and status["retry_at"] > now):
                return
            if status["status"] != failing:
                status["failures"] = 0  # a new kind of failure starts its own backoff
            status["failures"] += 1
            status.update(status=failing, reason=reason, cleared_by=cleared_by,
                          retry_at=now + garage_decide.backoff_seconds(status["failures"]))
        self.log(f"{repo}: {failing} — {reason}")

    def poll(self) -> dict[str, list[dict]]:
        now = self.clock()
        wanted: dict[str, list[dict]] = {}
        running: dict[str, str] = {}
        for repo, entry in sorted(self.cfg["repos"].items()):
            with self._lock:
                status = self.repo_status[repo]
                if status["status"] == "blocked" and now < status["retry_at"]:
                    continue
            invalid = None
            try:
                branch = self.api.repo_info(repo)["default_branch"]
                jobs = self.api.active_jobs(repo)
                section = garage_config.parse_runner_section(
                    self.api.file_text(repo, ".pitcall/config.json", branch))
            except (garage_github.ContentError, garage_config.ConfigError) as exc:
                invalid = str(exc)  # the repo's own content: its own config invalid, below
            except garage_github.RateLimited as exc:
                self.paused_until = exc.retry_at
                self.log("rate limited by GitHub: polling paused until "
                         + time.strftime("%H:%M:%S", time.localtime(exc.retry_at)))
                break
            except garage_github.ApiError as exc:
                self._back_off(repo, "blocked", _reason(exc), "poll")
                continue
            except OSError as exc:  # TransportError too: a dropped connection or a cut-off reply
                self.log(f"GitHub unreachable, polling again next time: {exc}")
                break
            except Exception as exc:  # unforeseen: by type alone, and only this repo waits
                self._back_off(repo, "blocked", f"GitHub reply unreadable ({type(exc).__name__})",
                               "poll")
                continue
            for found in jobs:
                if found.get("status") == "in_progress" and found.get("runner_name"):
                    running[found["runner_name"]] = found.get("name")
            matching = garage_decide.wanted(jobs, entry["labels"])
            if invalid is not None:
                with self._lock:
                    if status["status"] != "config invalid":
                        self.log(f"{repo}: config invalid — {invalid}")
                    status.update(status="config invalid", reason=invalid, queued=len(matching),
                                  cleared_by="poll")
                continue
            self.sections[repo] = section
            with self._lock:
                status["queued"] = len(matching)
                if status["cleared_by"] == "poll":
                    status.update(status="ok", reason=None, retry_at=None, failures=0,
                                  cleared_by=None)
            wanted[repo] = matching
        for slot in self.slots.values():
            with slot.lock:
                if slot.registration_name in running:
                    slot.job = running[slot.registration_name]
        return wanted

    # --- handing out ----------------------------------------------------------------------------

    def _hand_out(self, wanted: dict[str, list[dict]], now: float) -> None:
        repos = sorted(self.cfg["repos"])
        eligible, probes = {}, set()
        with self._lock:
            for repo, jobs in wanted.items():
                status = self.repo_status.get(repo)
                if status is None:
                    continue
                if status["status"] == "ok":
                    eligible[repo] = jobs
                elif (status["status"] in ("setup failing", "blocked") and status["probe"] is None
                      and now >= status["retry_at"]):
                    eligible[repo] = jobs
                    probes.add(repo)
        pending: dict[tuple, int] = {}
        free = []
        for slot in self.slots.values():
            with slot.lock:
                if slot.state in PENDING and slot.repo is not None:
                    key = (slot.repo, garage_decide.label_key(slot.labels))
                    pending[key] = pending.get(key, 0) + 1
                elif slot.state in FREE and not slot.needs_cleanup:
                    free.append(slot.name)
        demand = garage_decide.demand(eligible, pending)
        for repo in probes & demand.keys():  # its wait is over: one runner tries it again
            first = min(demand[repo], key=lambda d: d.oldest)
            first.count = 1
            demand[repo] = [first]
        if not free or not repos or not any(demand.values()):
            return
        assignments, self.cursor = garage_decide.hand_out(free, demand, repos, self.cursor)
        for runner, repo, key in assignments:
            labels = garage_decide.confirmed_set(key, self.cfg["repos"][repo]["labels"])
            section = self.sections.get(repo, garage_config.RunnerSection())
            slot = self.slots[runner]
            with slot.lock:
                slot.state, slot.since, slot.repo, slot.labels = "starting", now, repo, labels
            if repo in probes:
                with self._lock:
                    self.repo_status[repo]["probe"] = runner
            self.log(f"{runner}: handed to {repo} ({', '.join(labels)})")
            self._start(lambda s=slot, r=repo, l=labels, c=section: self._run(s, r, l, c))

    def _run(self, slot, repo, labels, section) -> None:
        outcome = self.worker.run_job(slot, repo, labels, section)  # never raises: its backstop
        with self._lock:
            status = self.repo_status.get(repo)
            if status is None:  # the repo left while its job ran
                return
            if status["probe"] == slot.name:
                status["probe"] = None
            if outcome.kind == "setup failed":
                self._back_off(repo, "setup failing", _tail(outcome.detail), "job")
            elif outcome.kind == "api error" and isinstance(outcome.api_error,
                                                           garage_github.RateLimited):
                self.paused_until = outcome.api_error.retry_at
            elif outcome.kind == "api error" and outcome.api_error is not None:
                self._back_off(repo, "blocked", _reason(outcome.api_error), "job")
            elif outcome.kind == "done" and status["cleared_by"] == "job":
                status.update(status="ok", reason=None, retry_at=None, failures=0,
                              cleared_by=None)

    # --- the loop -------------------------------------------------------------------------------

    def tick(self) -> None:
        self.reload_config()
        self.threads = [t for t in self.threads if t.is_alive()]
        now = self.clock()
        if self.paused_until is not None and now >= self.paused_until:
            self.paused_until = None
        if self.paused_until is None:
            wanted = self.poll()
            if self.paused_until is None:  # the poll itself may have hit a rate limit
                self._hand_out(wanted, now)
                deadline = self.cfg["pickup_deadline_minutes"] * 60
                for slot in self.slots.values():
                    self.worker.check_pickup(slot, now, deadline)
        idle = self.cfg["idle_timeout_minutes"] * 60
        for slot in self.slots.values():
            self.worker.stop_idle(slot, now, idle)
            if slot.state == "broken" and slot.retry_at is not None and now >= slot.retry_at:
                self._start(lambda s=slot: self.worker.retry(s, self.clock()))
        self.write_state()

    def reconcile(self) -> None:
        now = self.clock()
        for slot in self.slots.values():
            # After a crash or a reboot nothing is known about a runner: treat each as dirty and
            # let the worker clean and stop it, so it starts again from a known state.
            with slot.lock:
                slot.state, slot.since, slot.retry_at = "broken", now, now
                slot.needs_cleanup, slot.error = True, "not cleaned since the garage started"
            self._start(lambda s=slot: self.worker.retry(s, now))  # side by side, not in turn
        self._join(RECONCILE_SECONDS)  # every runner clean before a registration goes
        prefix = f"pitcall-{self.cfg['id']}-"
        for repo in sorted(self.cfg["repos"]):
            try:
                self._delete_stale(repo, prefix)
            except garage_github.RateLimited as exc:
                self.paused_until = exc.retry_at
                self.log("reconcile: rate limited by GitHub; the other stale registrations wait "
                         "for the next start")
                return
            except (garage_github.ApiError, OSError) as exc:
                self.log(f"reconcile: {repo}: {exc}")
            except Exception as exc:  # a cut-off or malformed reply: its text could quote the body
                self.log(f"reconcile: {repo}: GitHub reply unreadable ({type(exc).__name__})")

    def _delete_stale(self, repo: str, prefix: str) -> None:
        """Delete this garage's registrations in one repo, each on its own: one that fails does
        not keep the others. A rate limit ends the pass."""
        for found in self.api.runners(repo):
            if not found.get("name", "").startswith(prefix):
                continue
            try:
                deleted = self.api.delete_runner(repo, found["id"])
            except garage_github.RateLimited:
                raise
            except (garage_github.ApiError, OSError) as exc:
                self.log(f"reconcile: {repo}: cannot delete {found['name']}: {exc}")
                continue
            if deleted:
                self.log(f"reconcile: deleted the stale registration {found['name']} in {repo}")
            else:
                self.log(f"reconcile: {repo}: GitHub kept the stale registration "
                         f"{found['name']}: it is busy")

    def shutdown(self, grace_seconds: float = SHUTDOWN_SECONDS) -> None:
        for slot in self.slots.values():
            with slot.lock:
                state, repo, job = slot.state, slot.repo, slot.job
            if state in ("waiting", "busy"):
                named = f" ({job})" if job else ""
                self.log(f"stopping: ending the job on {slot.name} for {repo}{named}; "
                         "it fails on GitHub")
            self.worker.end(slot)
        self._join(grace_seconds)
        for slot in self.slots.values():
            self._start(lambda s=slot: self._park(s))
        self._join(PARK_SECONDS)
        self.write_state()

    def _park(self, slot) -> None:
        for command in ("cleanup", "stop"):
            result = self.recipe.run(command, slot.name)
            if result.returncode != 0:
                self.log(f"stopping: {command} failed on {slot.name}: {_tail(result.output)}")
        with slot.lock:
            slot.state, slot.since, slot.container_running = "stopped", self.clock(), False

    # --- the state file -------------------------------------------------------------------------

    def state(self) -> dict:
        runners = {}
        for name, slot in self.slots.items():
            with slot.lock:
                runners[name] = slot.to_state()
        with self._lock:
            repos = {repo: {key: status[key] for key in REPO_FIELDS}
                     for repo, status in sorted(self.repo_status.items())}
        return {"version": self.version, "pid": os.getpid(), "written_at": self.clock(),
                "config_error": self.config_error,
                "token_expiration": getattr(self.api, "token_expiration", None),
                "paused_until": self.paused_until, "runners": runners, "repos": repos}

    def write_state(self) -> None:
        garage_state.write_state(self.state_path, self.state())


class Stop:
    """The stop signal's handler. It notes when the signal came: the service manager's stop
    timeout runs from then, not from when the tick it interrupted returns."""

    def __init__(self, monotonic=time.monotonic):
        self.event = threading.Event()
        self.at: float | None = None
        self._monotonic = monotonic

    def __call__(self, *_signal) -> None:
        if self.at is None:
            self.at = self._monotonic()
        self.event.set()

    def left(self, budget: float) -> float:
        """What remains of `budget` seconds, counted from the signal."""
        if self.at is None:
            return budget
        return max(0.0, budget - (self._monotonic() - self.at))


def _where(exc: BaseException) -> str:
    """` at <file>:<line>` of the innermost frame: where it failed, with none of its text."""
    frames = traceback.extract_tb(exc.__traceback__)
    return f" at {Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else ""


def serve(garage: Garage, stop: Stop) -> None:
    """Reconcile, tick until stopped, then shut down within what the stop timeout has left."""
    garage.reconcile()
    garage.write_state()
    while not stop.event.is_set():
        try:
            garage.tick()
        except Exception as exc:  # a restart reconciles, which ends every running job
            # By type and place alone: an unforeseen exception's text could quote a reply body
            # or a secret.
            garage.log(f"tick failed, carrying on: {type(exc).__name__}{_where(exc)}")
        stop.event.wait(garage.cfg["poll_seconds"])
    garage.shutdown(stop.left(SHUTDOWN_SECONDS))


def main(argv=None) -> int:
    """The unit's ExecStart. `argv` is accepted for symmetry with the CLI and ignored."""
    if os.geteuid() == 0:
        _stderr("refusing to run as root: the garage runs as your own user, and root happens "
                "only inside a runner, through the recipe")
        return 1
    config_path = garage_paths.machine_config_path()
    try:
        cfg = garage_config.load_machine_config(config_path)
    except garage_config.ConfigError as exc:
        _stderr(str(exc))
        return 1
    if "id" not in cfg:
        _stderr(f"{config_path} has no id: run garage start, which writes it")
        return 1
    try:
        token = garage_github.read_token(garage_paths.expand(cfg["token_file"]))
    except garage_github.TokenFileError as exc:
        _stderr(str(exc))
        return 1
    version_file = Path(__file__).resolve().parent / "VERSION"
    version = version_file.read_text().strip() if version_file.is_file() else "unknown"
    # The recipe strings are run by /bin/sh, which expands their leading ~ itself.
    garage = Garage(config_path, cfg, garage_github.GitHub(token),
                    garage_recipe.Recipe(cfg["recipe"]), garage_paths.state_file_path(),
                    version=version)
    stop = Stop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    garage.log(f"started {version}: {len(garage.slots)} runners, {len(cfg['repos'])} repos")
    serve(garage, stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
