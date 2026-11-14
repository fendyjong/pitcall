"""The garage service (Task 13): the loop against a fake GitHub, the real recipe runner over a
fake Popen, a fake clock, and a spawn the test controls. Nothing sleeps and nothing leaves the
process."""

import http.client
import itertools
import json
import os
import sys
import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "garage"))
import garage_config  # noqa: E402
import garage_github  # noqa: E402
import garage_recipe  # noqa: E402
import garage_service  # noqa: E402
import garage_state  # noqa: E402

ID = "3f9c2a71"
A, B = "octo/app", "octo/site"
LINUX = ["self-hosted", "linux"]
SECRET = "jit-secret-value"
T0 = 1_763_100_000.0
PIDS = itertools.count(4000)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def job(labels=LINUX, job_id=1, status="queued", created="2026-11-14T10:00:00Z",
        runner_name=None, name="build"):
    return {"id": job_id, "name": name, "status": status, "labels": list(labels),
            "created_at": created, "runner_name": runner_name,
            "html_url": f"https://github.com/octo/app/actions/runs/1/job/{job_id}"}


class FakeApi:
    token_expiration = "2026-12-01 00:00:00 UTC"

    def __init__(self, events):
        self.events = events
        self.info, self.configs, self.jobs, self.errors = {}, {}, {}, {}
        self.registered, self.next_id = {}, 500

    def _call(self, method, repo, *args):
        self.events.append(("api", method, repo, *args))
        error = self.errors.get((method, repo))
        if error is not None:
            raise error

    def repo_info(self, repo):
        self._call("repo_info", repo)
        return self.info.get(repo, {"private": True, "default_branch": "main"})

    def file_text(self, repo, path, ref):
        self._call("file_text", repo, path, ref)
        return self.configs.get(repo)

    def active_jobs(self, repo):
        self._call("active_jobs", repo)
        return [dict(j) for j in self.jobs.get(repo, [])]

    def runners(self, repo):
        self._call("runners", repo)
        return [dict(r) for r in self.registered.values() if r["repo"] == repo]

    def runner(self, repo, runner_id):
        self._call("runner", repo, runner_id)
        found = self.registered.get(runner_id)
        return dict(found) if found else None

    def delete_runner(self, repo, runner_id):
        self._call("delete_runner", repo, runner_id)
        found = self.registered.get(runner_id)
        if found and found["busy"]:
            return False
        self.registered.pop(runner_id, None)
        return True

    def jit_config(self, repo, name, labels):
        self._call("jit_config", repo, name, tuple(labels))
        self.next_id += 1
        self.registered[self.next_id] = {"id": self.next_id, "name": name, "busy": False,
                                         "repo": repo}
        return self.next_id, SECRET

    def consume(self):
        """GitHub removes a one-job runner once its job is done."""
        self.registered.clear()


def make_popen(box):
    """A Popen the real Recipe drives: records each recipe call, fails the ones in box.fail."""

    class FakePopen:
        def __init__(self, argv, env=None, **kwargs):
            self.argv, self.env, self.kwargs, self.pid = argv, env, kwargs, next(PIDS)
            command, args = argv[2].split()[0], argv[4:]
            self.name = " ".join((command, *args))
            self.runner = env["PITCALL_RUNNER"]
            box.events.append(("recipe", self.name, self.runner, env.get("PITCALL_REPO")))
            box.procs.append(self)
            failing = (self.name, self.runner) in box.fail or (self.name, None) in box.fail
            self.returncode = 1 if failing else 0
            self.terminated = False
            self.released = threading.Event()
            if not (self.name == "exec job" and box.job_block):
                self.released.set()

        def communicate(self, *args, **kwargs):
            return (f"{self.name} output\n".encode() if self.returncode else b""), None

        def wait(self, timeout=None):
            box.job_started.set()
            assert self.released.wait(5), "the fake job never finished"
            box.on_job()
            return -15 if self.terminated else self.returncode

        def terminate(self):
            self.terminated = True
            self.released.set()

        def finish(self, code=0):
            self.returncode = code
            self.released.set()

    return FakePopen


def make_killpg(box):
    """Ending a job signals its process group: here, the fake process with that pid."""

    def killpg(pid, sig):
        next(p for p in box.procs if p.pid == pid).terminate()

    return killpg


def now_spawn(fn):
    """Runs the worker inline: the job is over when tick() returns."""
    fn()


class Later:
    """Holds spawned work: a runner handed out stays 'starting' until the test runs it."""

    def __init__(self):
        self.pending = []

    def __call__(self, fn):
        self.pending.append(fn)


def machine_text(repos, runners=2, **extra):
    cfg = {"id": ID, "runners": runners,
           "recipe": {c: c for c in ("start", "stop", "exec", "cleanup")},
           "token_file": "~/.config/pitcall/garage.token",
           "repos": {repo: {"labels": sets} for repo, sets in repos.items()}}
    cfg.update(extra)
    return json.dumps(cfg)


def rewrite(path, text):
    """Rewrite the config with an mtime that certainly moved."""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


@pytest.fixture
def world(tmp_path):
    def make(repos, runners=2, spawn=now_spawn, **extra):
        events, logs, clock = [], [], Clock(T0)
        api = FakeApi(events)
        box = SimpleNamespace(events=events, fail=set(), job_block=False,
                              job_started=threading.Event(), procs=[], on_job=api.consume)
        config = tmp_path / "garage.json"
        config.write_text(machine_text(repos, runners, **extra))
        cfg = garage_config.parse_machine_config(config.read_text())
        recipe = garage_recipe.Recipe(cfg["recipe"], base_env={}, popen=make_popen(box),
                                      killpg=make_killpg(box))
        state = tmp_path / "state.json"
        garage = garage_service.Garage(config, cfg, api, recipe, state, clock=clock, spawn=spawn,
                                       log=logs.append, version="0.17.0")
        return SimpleNamespace(garage=garage, api=api, box=box, clock=clock, logs=logs,
                               events=events, config=config, state=state)
    return make


def steps(w, runner=None):
    return [e[1] for e in w.events if e[0] == "recipe" and runner in (None, e[2])]


def api_calls(w, method, repo, since=0):
    return [e for e in w.events[since:] if e[:3] == ("api", method, repo)]


def join_all(w):
    for thread in list(w.garage.threads):
        thread.join(5)
        assert not thread.is_alive()


# --- handing runners out -------------------------------------------------------------------------


def test_one_queued_job_gets_one_runner_while_its_container_starts(world):
    later = Later()
    w = world({A: [LINUX]}, runners=2, spawn=later)
    w.api.jobs[A] = [job(labels=["Self-Hosted", "LINUX"])]
    w.garage.tick()
    w.garage.tick()
    assert sorted(s.state for s in w.garage.slots.values()) == ["starting", "stopped"]
    assert len(later.pending) == 1


def test_repos_take_turns(world):
    later = Later()
    w = world({A: [LINUX], B: [LINUX]}, runners=3, spawn=later)
    w.api.jobs[A] = [job(job_id=n) for n in (1, 2, 3)]
    w.api.jobs[B] = [job(job_id=9)]
    w.garage.tick()
    assert sorted(s.repo for s in w.garage.slots.values()) == [A, A, B]


def test_a_job_on_github_hosted_labels_is_never_served(world):
    w = world({A: [LINUX]})
    w.api.jobs[A] = [job(labels=["ubuntu-latest"])]
    w.garage.tick()
    assert steps(w) == []
    assert w.garage.repo_status[A]["queued"] == 0


def test_the_runner_gets_the_confirmed_spelling_of_the_set_waiting_longest(world):
    large = ["self-hosted", "linux", "Large"]
    w = world({A: [LINUX, large]}, runners=1)
    w.api.jobs[A] = [job(job_id=1, created="2026-11-14T10:05:00Z"),
                     job(labels=["SELF-HOSTED", "large", "linux"], job_id=2,
                         created="2026-11-14T10:00:00Z")]
    w.garage.tick()
    registered = [e for e in w.events if e[:2] == ("api", "jit_config")]
    assert registered[0][3:] == (f"pitcall-{ID}-r1-1", tuple(large))


def test_a_container_starts_on_demand_and_stops_at_the_idle_timeout(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert steps(w) == ["start", "exec job", "cleanup"]
    w.api.jobs[A] = []
    w.clock.t += 599
    w.garage.tick()
    assert "stop" not in steps(w)
    w.clock.t += 1
    w.garage.tick()
    assert steps(w)[-1] == "stop"
    assert w.garage.slots["r1"].state == "stopped"


# --- the repo's runner section -------------------------------------------------------------------


def test_setup_comes_from_the_default_branch_and_runs_again_when_it_changes(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.info[A] = {"private": True, "default_branch": "trunk"}
    w.api.configs[A] = json.dumps({"runner": {"setup": "echo one"}})
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert ("api", "file_text", A, ".pitcall/config.json", "trunk") in w.events
    w.api.configs[A] = json.dumps({"runner": {"setup": "echo two"}})
    w.garage.tick()  # the fake still lists the job as queued: a second job
    setups = [p.env["PITCALL_SETUP"] for p in w.box.procs if p.name == "exec setup"]
    assert setups == [garage_recipe.setup_script(A, "echo one"),
                      garage_recipe.setup_script(A, "echo two")]


def test_a_failing_setup_marks_the_repo_and_waits_out_its_backoff(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.configs[A] = json.dumps({"runner": {"setup": "false"}})
    w.api.jobs[A] = [job()]
    w.box.fail.add(("exec setup", None))
    w.garage.tick()
    status = w.garage.repo_status[A]
    assert status["status"] == "setup failing"
    assert "exec setup output" in status["reason"]
    assert w.garage.slots["r1"].state == "idle"
    w.clock.t += 59
    w.garage.tick()
    assert steps(w).count("exec setup") == 1
    w.clock.t += 1
    w.garage.tick()
    assert steps(w).count("exec setup") == 2
    assert status["retry_at"] == w.clock.t + 120


def test_setup_failing_on_every_runner_at_once_is_one_failure_and_then_one_probe(world):
    later = Later()
    w = world({A: [LINUX]}, runners=3, spawn=later)
    w.api.configs[A] = json.dumps({"runner": {"setup": "false"}})
    w.api.jobs[A] = [job(job_id=n) for n in (1, 2, 3)]
    w.box.fail.add(("exec setup", None))
    w.garage.tick()
    while later.pending:
        later.pending.pop(0)()
    status = w.garage.repo_status[A]
    assert steps(w).count("exec setup") == 3
    assert status["retry_at"] == T0 + 60  # one round of failures waits the first minute
    w.clock.t = T0 + 60
    w.garage.tick()
    w.garage.tick()  # the probe is still starting: no second runner joins it
    assert len(later.pending) == 1
    later.pending.pop()()
    assert steps(w).count("exec setup") == 4
    assert status["retry_at"] == T0 + 60 + 120


def test_a_repo_whose_env_breaks_the_rules_is_config_invalid_while_others_are_served(world):
    w = world({A: [LINUX], B: [LINUX]}, runners=2)
    w.api.configs[A] = json.dumps({"runner": {"env": {"PITCALL_TOKEN": "x"}}})
    w.api.jobs[A] = [job(job_id=1)]
    w.api.jobs[B] = [job(job_id=2)]
    w.garage.tick()
    status = w.garage.repo_status[A]
    assert status["status"] == "config invalid" and status["reason"]
    assert status["queued"] == 1
    served = [e[3] for e in w.events if e[0] == "recipe" and e[1] == "exec job"]
    assert served == [B]


# --- GitHub failures -----------------------------------------------------------------------------


def test_a_repo_github_refuses_is_blocked_rechecked_with_backoff_and_recovers(world):
    w = world({A: [LINUX], B: [LINUX]}, runners=1)
    w.api.errors[("repo_info", A)] = garage_github.ApiError(403, f"/repos/{A}")
    w.garage.tick()
    status = w.garage.repo_status[A]
    assert status["status"] == "blocked" and "403" in status["reason"]
    assert w.garage.repo_status[B]["status"] == "ok"
    w.clock.t += 59
    w.garage.tick()
    assert len(api_calls(w, "repo_info", A)) == 1
    del w.api.errors[("repo_info", A)]
    w.clock.t += 1
    w.garage.tick()
    assert len(api_calls(w, "repo_info", A)) == 2
    assert status["status"] == "ok" and status["reason"] is None


def test_a_github_server_error_backs_off_and_the_wait_doubles(world):
    w = world({A: [LINUX]})
    w.api.errors[("active_jobs", A)] = garage_github.ApiError(502, f"/repos/{A}/actions/runs")
    w.garage.tick()
    status = w.garage.repo_status[A]
    assert status["reason"] == "GitHub error 502"
    assert status["retry_at"] == T0 + 60
    w.clock.t = T0 + 60
    w.garage.tick()
    assert status["retry_at"] == T0 + 60 + 120


def test_a_rate_limit_pauses_all_polling_until_its_reset(world):
    w = world({A: [LINUX], B: [LINUX]})
    w.api.errors[("active_jobs", A)] = garage_github.RateLimited(
        403, f"/repos/{A}/actions/runs", T0 + 300)
    w.garage.tick()
    assert w.garage.paused_until == T0 + 300
    assert api_calls(w, "repo_info", B) == []  # the rest of the poll waited too
    before = len(w.events)
    w.clock.t = T0 + 299
    w.garage.tick()
    assert [e for e in w.events[before:] if e[0] == "api"] == []
    assert json.loads(w.state.read_text())["paused_until"] == T0 + 300
    del w.api.errors[("active_jobs", A)]
    w.clock.t = T0 + 300
    w.garage.tick()
    assert w.garage.paused_until is None
    assert api_calls(w, "repo_info", B, since=before)


def test_an_unreachable_github_is_logged_and_tried_again_next_tick(world):
    w = world({A: [LINUX]})
    w.api.errors[("repo_info", A)] = urllib.error.URLError("network is down")
    w.garage.tick()
    assert w.garage.repo_status[A]["status"] == "ok"
    assert any("unreachable" in line for line in w.logs)
    del w.api.errors[("repo_info", A)]
    before = len(w.events)
    w.garage.tick()
    assert api_calls(w, "active_jobs", A, since=before)


def test_a_garbled_github_reply_ends_the_poll_without_blocking_or_quoting_it(world):
    w = world({A: [LINUX], B: [LINUX]})
    error = http.client.IncompleteRead(b"")
    w.api.errors[("active_jobs", A)] = error
    w.garage.tick()
    assert w.garage.repo_status[A]["status"] == "ok" and w.garage.paused_until is None
    assert api_calls(w, "repo_info", B) == []  # the rest of the poll waits for the next tick
    assert any("IncompleteRead" in line for line in w.logs)
    assert not any("bytes read" in line for line in w.logs)  # str(error): its text, not its type


def test_github_refusing_a_registration_blocks_the_repo(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.errors[("jit_config", A)] = garage_github.ApiError(
        403, f"/repos/{A}/actions/runners/generate-jitconfig")
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert w.garage.repo_status[A]["status"] == "blocked"
    assert w.garage.slots["r1"].state == "idle"


def test_a_registration_github_keeps_refusing_waits_longer_each_round_until_a_job_runs(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.errors[("jit_config", A)] = garage_github.ApiError(
        403, f"/repos/{A}/actions/runners/generate-jitconfig")
    w.api.jobs[A] = [job()]
    status = w.garage.repo_status[A]
    waits = []
    for _ in range(3):
        w.garage.tick()
        assert status["status"] == "blocked"
        waits.append(status["retry_at"] - w.clock.t)
        w.clock.t = status["retry_at"] - 1
        before = len(w.events)
        w.garage.tick()
        assert status["status"] == "blocked"
        assert api_calls(w, "jit_config", A, since=before) == []
        w.clock.t += 1
    assert waits == [60, 120, 240]
    del w.api.errors[("jit_config", A)]
    w.garage.tick()  # the next probe registers, and its job runs
    assert status["status"] == "ok" and status["failures"] == 0


# --- reconcile -----------------------------------------------------------------------------------


def test_reconcile_cleans_every_runner_before_deleting_only_this_garages_registrations(world):
    w = world({A: [LINUX]}, runners=2)
    w.api.registered = {
        1: {"id": 1, "name": f"pitcall-{ID}-r1-4", "busy": False, "repo": A},
        2: {"id": 2, "name": "pitcall-0a0b0c0e-r1-1", "busy": False, "repo": A},
        3: {"id": 3, "name": "my-own-runner", "busy": False, "repo": A},
    }
    w.garage.reconcile()
    flat = [(e[1], e[2]) if e[0] == "recipe" else (e[1],) for e in w.events]
    assert flat[:4] == [("cleanup", "r1"), ("stop", "r1"), ("cleanup", "r2"), ("stop", "r2")]
    assert [e for e in w.events if e[:2] == ("api", "delete_runner")] == [
        ("api", "delete_runner", A, 1)]
    assert sorted(w.api.registered) == [2, 3]
    assert all(s.state == "stopped" for s in w.garage.slots.values())


def test_a_runner_reconcile_cannot_clean_is_broken_and_the_others_are_not(world):
    w = world({A: [LINUX]}, runners=2)
    w.box.fail.add(("cleanup", "r1"))
    w.garage.reconcile()
    r1, r2 = w.garage.slots["r1"], w.garage.slots["r2"]
    assert r1.state == "broken" and r1.needs_cleanup and r1.retry_at == T0 + 60
    assert r2.state == "stopped"


def test_reconcile_does_not_claim_a_delete_github_refused(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.registered = {1: {"id": 1, "name": f"pitcall-{ID}-r1-4", "busy": True, "repo": A}}
    w.garage.reconcile()
    assert any("kept" in line and f"pitcall-{ID}-r1-4" in line for line in w.logs)
    assert not any("deleted" in line for line in w.logs)


def test_a_garbled_reply_during_reconcile_skips_only_that_repo(world):
    w = world({A: [LINUX], B: [LINUX]}, runners=1)
    w.api.errors[("runners", A)] = http.client.IncompleteRead(b"")
    w.api.registered = {1: {"id": 1, "name": f"pitcall-{ID}-r1-4", "busy": False, "repo": B}}
    w.garage.reconcile()
    assert w.api.registered == {}
    assert any(A in line and "IncompleteRead" in line for line in w.logs)
    assert not any("bytes read" in line for line in w.logs)


# --- the machine config --------------------------------------------------------------------------


def test_a_config_that_no_longer_parses_leaves_the_service_on_the_last_one(world):
    w = world({A: [LINUX]}, runners=1)
    rewrite(w.config, "{ not json")
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert w.garage.config_error
    assert any("keeping the last" in line for line in w.logs)
    assert steps(w) == ["start", "exec job", "cleanup"]  # still serving A
    assert json.loads(w.state.read_text())["config_error"] == w.garage.config_error
    rewrite(w.config, machine_text({A: [LINUX]}, 1))
    w.garage.tick()
    assert w.garage.config_error is None


def test_joining_and_leaving_need_no_restart(world):
    w = world({A: [LINUX]}, runners=1)
    rewrite(w.config, machine_text({A: [LINUX], B: [LINUX]}, 1))
    w.garage.tick()
    assert api_calls(w, "repo_info", B)
    rewrite(w.config, machine_text({B: [LINUX]}, 1))
    before = len(w.events)
    w.garage.tick()
    assert api_calls(w, "repo_info", A, since=before) == []
    assert A not in json.loads(w.state.read_text())["repos"]


def test_a_job_already_running_for_a_repo_that_left_carries_on(world):
    later = Later()
    w = world({A: [LINUX]}, runners=1, spawn=later)
    w.api.jobs[A] = [job()]
    w.garage.tick()
    rewrite(w.config, machine_text({}, 1))
    w.garage.tick()
    assert w.garage.slots["r1"].state == "starting" and len(later.pending) == 1
    later.pending.pop()()  # the job runs to the end even though its repo left
    assert w.garage.slots["r1"].state == "idle"


def test_a_changed_runner_count_waits_for_the_next_start(world):
    w = world({A: [LINUX]}, runners=2)
    rewrite(w.config, machine_text({A: [LINUX]}, 5))
    w.garage.tick()
    assert len(w.garage.slots) == 2
    assert any("runners" in line and "next garage start" in line for line in w.logs)


# --- secrets -------------------------------------------------------------------------------------


def test_the_jit_config_reaches_the_recipe_environment_and_nowhere_else(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.configs[A] = json.dumps({"runner": {"env": {"TZ": "UTC"}}})
    w.api.jobs[A] = [job()]
    w.garage.tick()
    proc = next(p for p in w.box.procs if p.name == "exec job")
    assert proc.env["ACTIONS_RUNNER_INPUT_JITCONFIG"] == SECRET
    assert proc.env["TZ"] == "UTC"
    assert not any(SECRET in arg for p in w.box.procs for arg in p.argv)
    assert not any(SECRET in line for line in w.logs)
    assert SECRET not in w.state.read_text()


# --- running jobs, on real threads ---------------------------------------------------------------


def test_the_pickup_deadline_runs_through_the_loop(world):
    w = world({A: [LINUX]}, runners=1, spawn=None)
    w.box.job_block = True
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert w.box.job_started.wait(5)
    assert w.garage.slots["r1"].state == "waiting"
    w.clock.t += 300
    w.garage.tick()
    join_all(w)
    order = [e[1] for e in w.events if e[1] in ("delete_runner", "cleanup")]
    assert order[:2] == ["delete_runner", "cleanup"]
    assert w.api.registered == {}
    assert w.garage.slots["r1"].state == "idle"


def test_the_job_a_runner_took_is_named_in_the_state(world):
    w = world({A: [LINUX]}, runners=1, spawn=None)
    w.box.job_block = True
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert w.box.job_started.wait(5)
    slot = w.garage.slots["r1"]
    w.api.jobs[A] = [job(status="in_progress", runner_name=slot.registration_name,
                         name="build (linux)")]
    w.garage.tick()
    assert slot.job == "build (linux)"
    assert json.loads(w.state.read_text())["runners"]["r1"]["job"] == "build (linux)"
    next(p for p in w.box.procs if p.name == "exec job").finish(0)
    join_all(w)


def test_stopping_ends_jobs_and_stops_every_runner(world):
    w = world({A: [LINUX]}, runners=2, spawn=None)
    w.box.job_block = True
    w.api.jobs[A] = [job()]
    w.garage.tick()
    assert w.box.job_started.wait(5)
    w.garage.shutdown(grace_seconds=5)
    assert any("ending the job" in line and A in line for line in w.logs)
    assert [e for e in w.events if e[:2] == ("api", "delete_runner")]
    assert next(p for p in w.box.procs if p.name == "exec job").terminated
    for runner in ("r1", "r2"):
        assert "stop" in steps(w, runner)
    runners = json.loads(w.state.read_text())["runners"]
    assert {entry["state"] for entry in runners.values()} == {"stopped"}


# --- the state file ------------------------------------------------------------------------------


def test_the_state_file_speaks_the_shared_vocabulary(world):
    w = world({A: [LINUX]}, runners=1)
    w.api.jobs[A] = [job()]
    w.garage.tick()
    state = json.loads(w.state.read_text())
    assert set(state) == {"version", "pid", "written_at", "config_error", "token_expiration",
                          "paused_until", "runners", "repos"}
    assert state["version"] == "0.17.0"
    assert state["token_expiration"] == FakeApi.token_expiration
    assert state["written_at"] == T0
    assert set(state["runners"]["r1"]) == {"state", "repo", "labels", "registration", "job",
                                           "error", "retry_at", "since"}
    assert set(state["repos"][A]) == {"status", "reason", "queued", "labels", "retry_at"}
    assert state["repos"][A]["labels"] == [LINUX]
    assert all(r["state"] in garage_state.RUNNER_STATES for r in state["runners"].values())
    assert all(r["status"] in garage_state.REPO_STATUSES for r in state["repos"].values())


# --- main ----------------------------------------------------------------------------------------


def test_the_service_refuses_to_run_as_root(monkeypatch, capsys):
    monkeypatch.setattr(garage_service.os, "geteuid", lambda: 0)
    assert garage_service.main([]) == 1
    assert "root" in capsys.readouterr().err


def test_the_service_refuses_a_config_without_an_id(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(garage_service.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "pitcall").mkdir()
    cfg = json.loads(machine_text({}))
    del cfg["id"]
    (tmp_path / "pitcall" / "garage.json").write_text(json.dumps(cfg))
    assert garage_service.main([]) == 1
    assert "garage start" in capsys.readouterr().err


def test_the_service_refuses_a_token_file_others_can_read(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(garage_service.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    token = tmp_path / "garage.token"
    token.write_text("test-token\n")
    token.chmod(0o644)
    (tmp_path / "pitcall").mkdir()
    (tmp_path / "pitcall" / "garage.json").write_text(machine_text({}, token_file=str(token)))
    assert garage_service.main([]) == 1
    assert "chmod 600" in capsys.readouterr().err
