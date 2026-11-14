"""The runner worker (Task 11): one slot's lifecycle against a fake GitHub, a fake recipe and a
fake clock. Jobs that must stay running while the test acts run on a real thread, with a fake
process the test releases; nothing sleeps."""

import http.client
import itertools
import json
import signal
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "garage"))
import garage_config  # noqa: E402
import garage_github  # noqa: E402
import garage_recipe  # noqa: E402
import garage_state  # noqa: E402
import garage_worker  # noqa: E402

ID = "3f9c2a71"
REPO = "octo/app"
LABELS = ["Self-Hosted", "linux"]
SECRET = "jit-secret-value"
NOTHING = garage_config.RunnerSection()
PIDS = itertools.count(4000)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeProc:
    """`exec job`: returns at once, or blocks until the test (or a signal) releases it."""

    def __init__(self, code=0, block=False, on_exit=None):
        self.code, self.on_exit, self.terminated, self.pid = code, on_exit, False, next(PIDS)
        self.entered, self.released = threading.Event(), threading.Event()
        if not block:
            self.released.set()

    def wait(self):
        self.entered.set()
        assert self.released.wait(5), "the fake job never finished"
        if self.on_exit:
            self.on_exit()
        return -15 if self.terminated else self.code

    def terminate(self):
        self.terminated = True
        self.released.set()

    def finish(self, code=0):
        self.code = code
        self.released.set()


class FakeApi:
    def __init__(self, events):
        self.events, self.registered, self.next_id = events, {}, 100
        self.jit_error = self.runner_error = self.delete_error = None
        self.busy_on_delete = False

    def jit_config(self, repo, name, labels):
        self.events.append(("jit_config", repo, name, list(labels)))
        if self.jit_error:
            raise self.jit_error
        self.next_id += 1
        self.registered[self.next_id] = {"id": self.next_id, "name": name, "busy": False}
        return self.next_id, SECRET

    def runner(self, repo, runner_id):
        self.events.append(("runner", repo, runner_id))
        if self.runner_error:
            raise self.runner_error
        found = self.registered.get(runner_id)
        return dict(found) if found else None

    def delete_runner(self, repo, runner_id):
        self.events.append(("delete_runner", repo, runner_id))
        if self.delete_error:
            raise self.delete_error
        found = self.registered.get(runner_id)
        if found and self.busy_on_delete:  # a job arrives in the same moment
            found["busy"] = True
        if found and found["busy"]:
            return False
        self.registered.pop(runner_id, None)
        return True

    def consume(self):
        """GitHub removes a JIT runner once its one job is done."""
        self.registered.clear()


class FakeRecipe:
    def __init__(self, events, fail=(), proc=None):
        self.events, self.fail, self.proc, self.hooks, self.jobs = events, set(fail), proc, {}, []
        self.spawn_error = None

    def run(self, command, runner, repo=None, args=(), extra_env=None):
        name = " ".join((command, *args))
        self.events.append(("recipe", name, runner, repo, dict(extra_env or {})))
        if name in self.hooks:
            self.hooks[name]()
        code = 1 if name in self.fail else 0
        return garage_recipe.Result(code, f"{name} said no\n" if code else "")

    def spawn_job(self, runner, repo, variables):
        self.events.append(("recipe", "exec job", runner, repo, dict(variables)))
        self.jobs.append(dict(variables))
        if self.spawn_error:
            raise self.spawn_error
        return self.proc


@pytest.fixture
def world():
    events, logs, clock = [], [], Clock()
    api = FakeApi(events)

    def make(fail=(), proc=None, killpg=None):
        proc = proc or FakeProc(on_exit=api.consume)

        def signal_group(pid, sig):
            events.append(("killpg", pid, sig))
            proc.terminate()

        recipe = FakeRecipe(events, fail, proc)
        worker = garage_worker.Worker(ID, api, recipe, clock=clock, log=logs.append,
                                      killpg=killpg or signal_group)
        return worker, recipe

    return SimpleNamespace(events=events, logs=logs, clock=clock, api=api, make=make)


def handed(slot, repo=REPO, labels=LABELS):
    """What the service does before it starts run_job on a thread."""
    slot.state, slot.repo, slot.labels = "starting", repo, list(labels)
    return slot


def steps(events):
    return [e[1] for e in events if e[0] == "recipe"]


def names(events):
    return [e[1] if e[0] == "recipe" else e[0] for e in events]


def in_thread(fn, *args):
    box = {}
    thread = threading.Thread(target=lambda: box.update(outcome=fn(*args)))
    thread.start()
    return thread, box


def joined(thread, box):
    thread.join(5)
    assert not thread.is_alive(), "the worker never finished"
    return box["outcome"]


def calmly(fn, *args):
    """Call a worker method that must never raise: an escape fails the test on its own terms."""
    try:
        return fn(*args)
    except Exception as exc:
        pytest.fail(f"{fn.__name__} let {type(exc).__name__} escape")


def quoted(exc, world, slot):
    """Whether the worker logged or recorded the exception's own text (a reply, a secret)."""
    return any(str(exc) in text for text in [*world.logs, slot.error or ""])


# --- a job, start to finish ----------------------------------------------------------------------


def test_a_job_starts_registers_runs_and_cleans(world):
    worker, _ = world.make()
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "done"
    assert steps(world.events) == ["start", "exec job", "cleanup"]
    assert ("jit_config", REPO, f"pitcall-{ID}-r1-1", LABELS) in world.events
    # GitHub removed the one-job runner after its job, so nothing is left to delete; a job done
    # between two pickup checks (never seen busy) is not mistaken for a failure.
    assert not any(e[0] == "delete_runner" for e in world.events)
    assert slot.state == "idle" and slot.container_running
    assert slot.repo is None and slot.registration_id is None and slot.failures == 0


def test_the_jit_config_reaches_only_the_jobs_environment(world):
    worker, recipe = world.make()
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, garage_config.RunnerSection(env={"TZ": "UTC"}))
    assert recipe.jobs == [{"TZ": "UTC", "ACTIONS_RUNNER_INPUT_JITCONFIG": SECRET}]
    assert not any(SECRET in line for line in world.logs)
    assert SECRET not in json.dumps(slot.to_state())


def test_a_running_container_is_not_started_again(world):
    worker, _ = world.make()
    slot = handed(garage_worker.Slot("r1"))
    slot.container_running = True
    worker.run_job(slot, REPO, LABELS, NOTHING)
    assert steps(world.events) == ["exec job", "cleanup"]


def test_registration_names_count_up_per_runner(world):
    worker, _ = world.make()
    slot = garage_worker.Slot("r2")
    worker.run_job(handed(slot), REPO, LABELS, NOTHING)
    worker.run_job(handed(slot), REPO, LABELS, NOTHING)
    registered = [e[2] for e in world.events if e[0] == "jit_config"]
    assert registered == [f"pitcall-{ID}-r2-1", f"pitcall-{ID}-r2-2"]


# --- setup ---------------------------------------------------------------------------------------


def test_setup_runs_as_the_setup_script_before_the_job(world):
    worker, _ = world.make()
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, garage_config.RunnerSection(setup="apt-get install -y jq"))
    assert steps(world.events) == ["start", "exec setup", "exec job", "cleanup"]
    setup = next(e for e in world.events if e[0] == "recipe" and e[1] == "exec setup")
    assert setup[3] == REPO
    assert setup[4] == {"PITCALL_SETUP": garage_recipe.setup_script(REPO, "apt-get install -y jq")}


def test_a_failing_setup_frees_the_runner_without_breaking_it(world):
    worker, _ = world.make(fail={"exec setup"})
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, garage_config.RunnerSection(setup="false"))
    assert outcome.kind == "setup failed"
    assert "exec setup said no" in outcome.detail
    assert slot.state == "idle" and slot.failures == 0 and slot.repo is None
    assert not any(e[0] == "jit_config" for e in world.events)


# --- recipe and container failures ---------------------------------------------------------------


def test_a_failing_start_breaks_the_runner_and_backs_off(world):
    worker, _ = world.make(fail={"start"})
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "broken"
    assert slot.state == "broken" and slot.failures == 1
    assert slot.retry_at == world.clock.t + 60
    assert "start exited 1" in slot.error
    assert slot.repo is None
    assert not any(e[0] == "jit_config" for e in world.events)


def test_backoff_doubles_up_to_thirty_minutes(world):
    worker, _ = world.make(fail={"start"})
    slot = garage_worker.Slot("r1")
    waits = []
    for _ in range(7):
        worker.run_job(handed(slot), REPO, LABELS, NOTHING)
        waits.append(slot.retry_at - world.clock.t)
    assert waits == [60, 120, 240, 480, 960, 1800, 1800]


def test_an_api_error_on_registering_frees_the_runner(world):
    worker, _ = world.make()
    world.api.jit_error = garage_github.ApiError(403, f"/repos/{REPO}/actions/runners/generate-jitconfig")
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "api error"
    assert outcome.api_error is world.api.jit_error
    assert slot.state == "idle" and slot.container_running
    assert "exec job" not in steps(world.events)


def test_a_job_that_exits_before_pickup_breaks_the_runner(world):
    worker, _ = world.make(proc=FakeProc(code=1))  # GitHub still lists the registration
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "broken"
    assert "before its runner picked up a job" in slot.error
    assert steps(world.events) == ["start", "exec job", "cleanup"]
    assert any(e[0] == "delete_runner" for e in world.events)
    assert world.api.registered == {}


def test_a_job_that_cannot_be_spawned_breaks_the_runner_and_drops_its_registration(world):
    worker, recipe = world.make()
    recipe.spawn_error = FileNotFoundError(2, "No such file or directory", "/bin/sh")
    slot = handed(garage_worker.Slot("r1"))
    try:
        outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    except OSError:
        pytest.fail("a job that cannot be spawned escaped run_job and left its slot stuck")
    assert outcome.kind == "broken"
    assert slot.state == "broken" and "exec job could not start" in slot.error
    assert "/bin/sh" in slot.error
    assert slot.failures == 1 and slot.retry_at == world.clock.t + 60
    assert slot.repo is None and slot.registration_id is None
    assert any(e[0] == "delete_runner" for e in world.events) and world.api.registered == {}
    assert not any(SECRET in line for line in world.logs)


def test_a_runner_that_dies_mid_job_is_cleaned_and_freed_and_the_job_not_rerun(world):
    proc = FakeProc(block=True)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    rid = slot.registration_id
    world.api.registered[rid]["busy"] = True
    worker.check_pickup(slot, world.clock.t, 300)
    assert slot.state == "busy" and slot.seen_busy
    world.api.registered[rid]["busy"] = False  # the container died: GitHub lists it, offline
    proc.finish(137)
    outcome = joined(thread, box)
    assert outcome.kind == "done"
    assert slot.state == "idle" and world.api.registered == {}
    order = names(world.events)
    assert order.index("cleanup") < len(order) - 1 - order[::-1].index("delete_runner")
    assert order.count("jit_config") == 1


def test_a_failed_cleanup_keeps_the_runner_from_jobs_until_a_retry_succeeds(world):
    worker, recipe = world.make(fail={"cleanup"})
    slot = handed(garage_worker.Slot("r1"))
    outcome = worker.run_job(slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "broken"
    assert slot.state == "broken" and slot.needs_cleanup
    assert worker.retry(slot, world.clock.t + 59) is False
    recipe.fail.clear()
    world.clock.t += 60
    del world.events[:]
    assert worker.retry(slot, world.clock.t) is True
    assert steps(world.events) == ["cleanup", "stop"]
    assert slot.state == "stopped" and not slot.container_running
    assert not slot.needs_cleanup and slot.error is None


def test_a_retry_that_fails_backs_off_further(world):
    worker, recipe = world.make(fail={"start"})
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, NOTHING)
    recipe.fail = {"cleanup"}
    world.clock.t += 60
    assert worker.retry(slot, world.clock.t) is False
    assert slot.state == "broken" and slot.failures == 2 and slot.needs_cleanup
    assert slot.retry_at == world.clock.t + 120


def test_a_retry_runs_once_even_when_asked_twice(world):
    worker, recipe = world.make(fail={"start"})
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, NOTHING)
    recipe.fail.clear()
    world.clock.t += 60
    inner = []
    recipe.hooks["cleanup"] = lambda: inner.append(worker.retry(slot, world.clock.t))
    assert worker.retry(slot, world.clock.t) is True
    assert inner == [False]


# --- what GitHub's client or Popen raise beyond ApiError and OSError -----------------------------


def test_a_malformed_registration_reply_breaks_the_runner_for_a_clean_retry(world):
    worker, _ = world.make()
    world.api.jit_error = error = json.JSONDecodeError("reply-text-that-must-not-leak", "{", 0)
    slot = handed(garage_worker.Slot("r1"))
    outcome = calmly(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "broken"
    assert slot.state == "broken" and slot.needs_cleanup and "JSONDecodeError" in slot.error
    assert slot.failures == 1 and slot.retry_at == world.clock.t + 60 and slot.repo is None
    assert world.api.registered == {} and "exec job" not in steps(world.events)
    assert not quoted(error, world, slot)


def test_a_cut_off_lookup_after_the_job_still_runs_cleanup(world):
    worker, _ = world.make(proc=FakeProc())  # the registration is still listed after the job
    world.api.runner_error = error = http.client.IncompleteRead(b"partial-reply-that-must-not-leak")
    slot = handed(garage_worker.Slot("r1"))
    calmly(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert steps(world.events) == ["start", "exec job", "cleanup"]
    assert world.api.registered == {}  # it could not say, so the registration was deleted anyway
    assert slot.state == "idle" and not slot.needs_cleanup
    assert any("IncompleteRead" in line for line in world.logs) and not quoted(error, world, slot)


def test_a_pickup_check_that_cannot_read_the_reply_leaves_the_runner_waiting(world):
    proc = FakeProc(block=True, on_exit=world.api.consume)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    world.api.runner_error = error = http.client.BadStatusLine("status-line-that-must-not-leak")
    calmly(worker.check_pickup, slot, slot.since + 300, 300)
    assert slot.state == "waiting" and not proc.terminated  # checked again on the next poll
    assert any("BadStatusLine" in line for line in world.logs) and not quoted(error, world, slot)
    world.api.runner_error = None
    proc.finish(0)
    assert joined(thread, box).kind == "done"


def test_ending_signals_the_job_even_when_its_registration_cannot_be_deleted(world):
    proc = FakeProc(block=True)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    world.api.delete_error = error = json.JSONDecodeError("reply-text-that-must-not-leak", "{", 0)
    calmly(worker.end, slot)
    assert ("killpg", proc.pid, signal.SIGTERM) in world.events
    assert joined(thread, box).kind == "ended"
    assert any("JSONDecodeError" in line for line in world.logs) and not quoted(error, world, slot)


def test_a_job_whose_environment_popen_rejects_breaks_the_runner_and_drops_its_registration(world):
    worker, recipe = world.make()
    recipe.spawn_error = error = ValueError("embedded null byte")
    slot = handed(garage_worker.Slot("r1"))
    outcome = calmly(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert outcome.kind == "broken"
    assert slot.state == "broken" and slot.needs_cleanup and "ValueError" in slot.error
    assert slot.registration_id is None and world.api.registered == {}
    assert not quoted(error, world, slot)


# --- the pickup deadline -------------------------------------------------------------------------


def test_an_unpicked_registration_is_deleted_at_the_deadline_then_the_job_ends(world):
    proc = FakeProc(block=True)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    assert slot.state == "waiting"
    since = slot.since
    worker.check_pickup(slot, since + 299, 300)
    assert not proc.terminated and slot.state == "waiting"
    worker.check_pickup(slot, since + 300, 300)
    outcome = joined(thread, box)
    assert proc.terminated and outcome.kind == "ended"
    assert ("killpg", proc.pid, signal.SIGTERM) in world.events  # the whole job, not its shell
    order = names(world.events)
    # cleanup runs on the worker thread and can trail a late delete; killpg pins the order.
    assert order.index("delete_runner") < order.index("killpg") < order.index("cleanup")
    assert slot.state == "idle" and world.api.registered == {}
    assert slot.failures == 0 and not slot.ended_by_garage


def test_a_job_arriving_as_the_registration_is_deleted_keeps_running(world):
    proc = FakeProc(block=True, on_exit=world.api.consume)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    world.api.busy_on_delete = True
    worker.check_pickup(slot, slot.since + 300, 300)
    assert slot.state == "busy" and not proc.terminated
    proc.finish(0)
    assert joined(thread, box).kind == "done"


def test_a_registration_github_no_longer_lists_counts_as_picked_up(world):
    proc = FakeProc(block=True)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    world.api.consume()
    worker.check_pickup(slot, slot.since + 300, 300)
    assert slot.state == "busy" and slot.seen_busy and not proc.terminated
    proc.finish(0)
    assert joined(thread, box).kind == "done"


# --- idle stop and shutdown ----------------------------------------------------------------------


def test_an_idle_container_stops_at_the_idle_timeout(world):
    worker, _ = world.make()
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, NOTHING)
    del world.events[:]
    idle_since = slot.since
    assert worker.stop_idle(slot, idle_since + 599, 600) is False
    assert worker.stop_idle(slot, idle_since + 600, 600) is True
    assert steps(world.events) == ["stop"]
    assert slot.state == "stopped" and not slot.container_running


def test_a_failing_stop_breaks_the_runner(world):
    worker, _ = world.make(fail={"stop"})
    slot = handed(garage_worker.Slot("r1"))
    worker.run_job(slot, REPO, LABELS, NOTHING)
    assert worker.stop_idle(slot, slot.since + 600, 600) is False
    assert slot.state == "broken" and "stop exited 1" in slot.error


def test_ending_a_waiting_runner_deletes_its_registration_and_ends_its_job(world):
    proc = FakeProc(block=True)
    worker, _ = world.make(proc=proc)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    rid = slot.registration_id
    worker.end(slot)
    assert joined(thread, box).kind == "ended"
    assert ("killpg", proc.pid, signal.SIGTERM) in world.events
    assert ("delete_runner", REPO, rid) in world.events
    assert world.api.registered == {}


def test_ending_a_job_that_already_exited_is_not_an_error(world):
    proc, signalled = FakeProc(block=True), []

    def already_gone(pid, sig):
        signalled.append((pid, sig))
        proc.finish(0)  # the job exited on its own a moment before the signal
        raise ProcessLookupError(pid)

    worker, _ = world.make(proc=proc, killpg=already_gone)
    slot = handed(garage_worker.Slot("r1"))
    thread, box = in_thread(worker.run_job, slot, REPO, LABELS, NOTHING)
    assert proc.entered.wait(5)
    try:
        worker.end(slot)
    except ProcessLookupError:
        pytest.fail("a job group that is already gone must count as ended")
    assert signalled == [(proc.pid, signal.SIGTERM)]
    assert joined(thread, box).kind == "ended"


def test_ending_during_setup_registers_nothing(world):
    worker, recipe = world.make()
    slot = handed(garage_worker.Slot("r1"))
    recipe.hooks["exec setup"] = lambda: worker.end(slot)
    outcome = worker.run_job(slot, REPO, LABELS, garage_config.RunnerSection(setup="true"))
    assert outcome.kind == "ended"
    assert not any(e[0] == "jit_config" for e in world.events)
    assert slot.state == "idle" and not slot.ended_by_garage


def test_to_state_is_the_state_files_runner_entry(world):
    slot = garage_worker.Slot("r2")
    entry = slot.to_state()
    assert set(entry) == {"state", "repo", "labels", "registration", "job", "error", "retry_at", "since"}
    assert entry["state"] in garage_state.RUNNER_STATES
