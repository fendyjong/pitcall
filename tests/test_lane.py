"""The lane's exclusion, and the resolution the exclusion depends on.

Every caller must land on the SAME lock file or there is no exclusion at all,
so the resolution tests below build real repositories — a project, a linked
worktree, a submodule, a plain directory — and ask git the same question the
module asks. They are not mocked: the property under test *is* git's answer,
and a fake one would assert only that the fake was written correctly.
"""
import importlib
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lane  # noqa: E402
import lane_config  # noqa: E402

# Identity and protocol settings passed per-command so these repositories build
# on a machine with no global git config at all — a fresh CI runner has none,
# and `git commit` refuses without an author.
_GIT_ID = (
    "-c", "user.email=lane@example.invalid",
    "-c", "user.name=lane tests",
    "-c", "commit.gpgsign=false",
    "-c", "init.defaultBranch=main",
    # Adding a submodule from a local path is a file-transport clone, refused
    # by default since git 2.38.
    "-c", "protocol.file.allow=always",
)


def _git(*args, cwd):
    out = subprocess.run(["git", *_GIT_ID, *args], cwd=str(cwd),
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _new_repo(path: Path) -> Path:
    """A real repository with one commit, resolved of any symlinks.

    The commit matters: `git worktree add` has nothing to check out without
    one, and a submodule cannot be added from a repository with no HEAD.
    """
    path.mkdir(parents=True, exist_ok=True)
    path = path.resolve()
    _git("init", "-q", ".", cwd=path)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=path)
    return path


# --- Resolving the project, not the module -------------------------------
#
# This module ships in a plugin, installed outside any project, so its own
# location says nothing about which project a session is working in — only the
# caller's location does. That makes the resolution the load-bearing part: get
# it wrong and each caller quietly gets a lane of its own, which looks exactly
# like a working one and excludes nobody.


def test_lane_dir_is_the_callers_project_not_the_modules_own_repository(tmp_path, monkeypatch):
    project = _new_repo(tmp_path / "project")
    monkeypatch.chdir(project)
    assert lane.lane_dir() == project / ".pitcall"


def test_every_worktree_of_one_project_resolves_to_one_lane(tmp_path, monkeypatch):
    """The property the whole module exists for.

    Sessions work in separate worktrees of the same project. The lane is
    scoped to the shared git dir rather than the invoking worktree, so each
    of them takes the same lock; a worktree-scoped lane would hand every
    session its own and serialise nothing.
    """
    project = _new_repo(tmp_path / "project")
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=project)

    monkeypatch.chdir(project)
    from_main = lane.lane_dir()
    monkeypatch.chdir(linked)
    assert lane.lane_dir() == from_main
    # Two callers agreeing on the SAME WRONG directory would satisfy the line
    # above, so name the one they have to agree on.
    assert from_main == project / ".pitcall"


def test_the_lock_is_shared_while_the_checkout_and_config_are_the_callers(
        tmp_path, monkeypatch):
    """The two roots are different questions, and this is the only place they
    give different answers.

    - which lock file? the SHARED root, or two sessions each hold "the lane"
      and both bring the stack up;
    - which checkout does `bringup` run in, and whose config? the CALLER's, or
      a session validates code it is not working on and gets a plausible-looking
      result for a branch it never touched.

    In the main checkout both are the same path, so nothing there can tell a
    correct implementation from one that collapsed them back into a single
    root. A linked worktree can, which is what this test is for.
    """
    project = _new_repo(tmp_path / "project")
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=project)
    linked = linked.resolve()
    assert linked != project, "the two roots must actually differ, or this proves nothing"

    monkeypatch.chdir(linked)

    # The lock is shared with every other worktree of this project.
    assert lane.lane_dir() == project / ".pitcall"

    # The config is the one committed on THIS branch, in THIS checkout.
    assert lane_config.config_path() == linked / lane_config.CONFIG_NAME

    # And `lane run` defaults its worktree to the caller's own checkout. Asserted
    # through main() rather than by re-deriving it, because the wiring of the
    # default is the part that was wrong.
    seen = {}
    monkeypatch.setattr(lane, "_cmd_run",
                        lambda session, worktree: (seen.update(worktree=worktree), 0)[1])
    assert lane.main(["run", "--session", "sess-a"]) == 0
    assert seen["worktree"] == str(linked)


def test_lane_dir_from_inside_a_submodule_is_the_superprojects(tmp_path, monkeypatch):
    """A submodule is its own repository, and asking git from inside one
    answers about *it*.

    A session working in a submodule of a project is working on that project,
    and must queue behind the sessions working elsewhere in it. Resolving to
    the submodule instead yields a second, private lane with no error
    anywhere — the failure this climb exists to prevent.
    """
    project = _new_repo(tmp_path / "project")
    dependency = _new_repo(tmp_path / "dependency")
    _git("submodule", "add", "-q", str(dependency), "vendor/dependency", cwd=project)
    inside = project / "vendor" / "dependency"

    monkeypatch.chdir(inside)
    # Sanity: git really does answer differently from in here. Without this the
    # assertion below could pass while proving nothing.
    own = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=inside)
    assert Path(own).parent != project, \
        "expected the submodule to resolve elsewhere — otherwise this test is vacuous"

    assert lane.lane_dir() == project / ".pitcall"


def test_a_worktree_of_a_submodule_still_resolves_the_projects_lane(tmp_path, monkeypatch):
    """The case the climb alone does not reach.

    `--show-superproject-working-tree` answers from a submodule's checked-out
    directory, but a LINKED WORKTREE of that submodule is not the gitlink path,
    so git returns nothing and the climb stops inside the submodule. The naive
    answer is then `dirname(<project>/.git/modules/libs/a)` — a lane inside the
    superproject's git dir that the project itself never resolves to.

    Two sessions, one in the project and one in a worktree of its submodule,
    would each hold "the lane" and both bring the stack up, with nothing
    erroring. That is the whole failure this module exists to prevent, so it is
    pinned against the project path this test BUILT — never against the other
    root, which is wrong in lockstep whenever the climb stops early.
    """
    project = _new_repo(tmp_path / "project")
    dependency = _new_repo(tmp_path / "dependency")
    _git("submodule", "add", "-q", str(dependency), "libs/a", cwd=project)
    sub_worktree = tmp_path / "submodule-worktree"
    _git("worktree", "add", "-q", str(sub_worktree), "-b", "side",
         cwd=project / "libs" / "a")
    sub_worktree = sub_worktree.resolve()

    monkeypatch.chdir(sub_worktree)
    # Non-vacuity: the wrong answer must be reachable from here. If a future git
    # resolves this on its own, this fails and asks a human — which is correct,
    # because the test would otherwise be proving nothing.
    naive = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir",
                      cwd=sub_worktree)).parent
    assert naive != project, "the wrong answer is not reachable — this test is vacuous"

    assert lane.lane_dir() == project / ".pitcall"
    assert ".git" not in lane.lane_dir().parts, "the lane must never live inside a git dir"
    # The lane has a defensible answer here; the CHECKOUT does not. The session
    # is working on the submodule, the bring-up belongs to the project, and
    # picking either silently would validate a checkout nobody on this branch
    # wrote and report green on it.
    with pytest.raises(RuntimeError, match="--worktree"):
        lane_config.worktree_root()


def test_worktrees_of_sibling_submodules_do_not_form_a_private_shared_lane(
        tmp_path, monkeypatch):
    """Two submodules under one directory make the failure cross-submodule.

    `libs/a` and `libs/b` both have their git dirs under `<project>/.git/modules/
    libs/`, so the naive answer collapses worktrees of BOTH onto one lane —
    which they share with each other and with nothing else. Neither shares with
    the project. Over-exclusion between two unrelated dependencies AND
    under-exclusion against the project they belong to, at the same time.
    """
    project = _new_repo(tmp_path / "project")
    worktrees = {}
    for name in ("a", "b"):
        dependency = _new_repo(tmp_path / f"dependency-{name}")
        _git("submodule", "add", "-q", str(dependency), f"libs/{name}", cwd=project)
        wt = tmp_path / f"worktree-{name}"
        _git("worktree", "add", "-q", str(wt), "-b", "side", cwd=project / "libs" / name)
        worktrees[name] = wt.resolve()

    naive = {}
    for name, wt in worktrees.items():
        monkeypatch.chdir(wt)
        naive[name] = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir",
                                cwd=wt)).parent
        assert lane.lane_dir() == project / ".pitcall"

    # Non-vacuity, and a record of the shape: naively the two siblings agree with
    # each other and disagree with the project — the exact inversion of what the
    # lane needs.
    assert naive["a"] == naive["b"] != project, \
        "the sibling collapse is not reproducible here — this test is vacuous"


def test_a_submodule_under_a_linked_worktree_of_the_project_resolves_the_project(
        tmp_path, monkeypatch):
    """The second nesting shape, which a `.git/modules` match alone does not cover.

    When the superproject is itself checked out into a linked worktree, its
    submodules' git dirs land at `<main>/.git/worktrees/<wt>/modules/<path>` —
    there is no `.git/modules` anywhere in that path. Measured, not assumed; the
    sanity assertion below fails if this layout ever stops being the case, which
    is the point of writing it down.

    This is the layout a session actually works in — a worktree of the project,
    with submodules initialised — so a fix that keyed on `.git/modules` alone
    would have left the reachable half of the defect live.
    """
    project = _new_repo(tmp_path / "project")
    dependency = _new_repo(tmp_path / "dependency")
    _git("submodule", "add", "-q", str(dependency), "libs/a", cwd=project)
    _git("commit", "-q", "-m", "add the submodule", cwd=project)
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=project)
    _git("submodule", "update", "--init", "-q", cwd=linked)
    linked = linked.resolve()

    # A submodule sitting in its normal place inside that linked worktree: the
    # lane is the project's, while the checkout being worked on is the worktree.
    monkeypatch.chdir(linked / "libs" / "a")
    assert lane.lane_dir() == project / ".pitcall"
    assert lane_config.worktree_root() == linked

    # And a linked worktree OF that submodule, where git stops answering.
    deep = tmp_path / "deep"
    _git("worktree", "add", "-q", str(deep), "-b", "deep-side", cwd=linked / "libs" / "a")
    deep = deep.resolve()
    monkeypatch.chdir(deep)
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=deep)
    assert ".git/modules" not in common, \
        "this layout is supposed to lack .git/modules — otherwise it is not the case under test"
    assert lane.lane_dir() == project / ".pitcall"
    # ...while "which checkout" REFUSES here rather than guessing. This
    # assertion replaces `".git" not in worktree_root().parts`, which was
    # written to pass rather than to pin: the main checkout satisfied it, so
    # did `linked`, and so would any wrong-but-clean path — which is precisely
    # how the wrong one got through. Name the failure, not the absence of one.
    with pytest.raises(RuntimeError, match="--worktree"):
        lane_config.worktree_root()


def test_a_submodule_of_a_submodule_resolves_the_project_and_refuses_a_checkout(
        tmp_path, monkeypatch):
    """Nesting depth changes nothing, in either direction.

    In place, the superproject climb runs twice and lands on the project, so
    both roots answer. In a worktree of the inner submodule the git-dir climb
    settles the lane and the checkout has no answer — same rule, one level
    deeper, and it is checked here because "the shape nobody enumerated" is how
    both previous defects in this module arrived.
    """
    project = _new_repo(tmp_path / "project")
    outer = _new_repo(tmp_path / "outer")
    inner = _new_repo(tmp_path / "inner")
    _git("submodule", "add", "-q", str(inner), "vendor/inner", cwd=outer)
    _git("commit", "-q", "-m", "add the inner submodule", cwd=outer)
    _git("submodule", "add", "-q", str(outer), "libs/outer", cwd=project)
    _git("commit", "-q", "-m", "add the outer submodule", cwd=project)
    _git("submodule", "update", "--init", "--recursive", "-q", cwd=project)

    # In place, two levels down: both roots answer, and the checkout is the
    # project because that is the checkout the session is working in.
    nested = project / "libs" / "outer" / "vendor" / "inner"
    assert nested.is_dir(), "the recursive checkout did not materialise"
    monkeypatch.chdir(nested)
    assert lane.lane_dir() == project / ".pitcall"
    assert lane_config.worktree_root() == project

    # A worktree of that inner submodule: the lane still resolves, the checkout
    # refuses.
    deep = tmp_path / "deep-inner"
    _git("worktree", "add", "-q", str(deep), "-b", "deep-side", cwd=nested)
    deep = deep.resolve()
    monkeypatch.chdir(deep)
    assert lane.lane_dir() == project / ".pitcall"
    with pytest.raises(RuntimeError, match="--worktree"):
        lane_config.worktree_root()


def test_lane_dir_outside_any_repository_raises_instead_of_using_cwd(tmp_path, monkeypatch):
    """Fail loudly. A lane created in whatever directory the caller happened to
    be standing in is indistinguishable from a working one and excludes
    nobody, so there is no fallback — not to cwd, not to the module's own
    location, not to a temporary directory."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    # Sanity: nothing above this directory is a repository, or the test is vacuous.
    probe = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                           cwd=str(plain), capture_output=True)
    assert probe.returncode != 0, "tmp_path is inside a repository — this test proves nothing"

    with pytest.raises(RuntimeError):
        lane.lane_dir()
    assert not (plain / ".pitcall").exists(), "a lane was created outside a project"


# --- The state machine ---------------------------------------------------


def test_acquire_succeeds_when_free(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a") as held:
        assert held is not None


def test_second_acquire_fails_while_held(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a"):
        assert lane.acquire("sess-b", "/wt/b") is None


def test_holder_is_recorded_once_in_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a"):
        st = lane.status()
        assert st["holder"]["session"] == "sess-a"
        assert st["holder"]["worktree"] == "/wt/a"
        # No DECISION keys on the pid, but it is not unread: status() surfaces it,
        # and that is the handle the "kill the stuck holder" recovery uses.
        assert st["holder"]["pid"] == os.getpid()
        assert "elapsed_seconds" in st
        # Derived for display, never stored a second time.
        raw = json.loads((tmp_path / "lane.lock").read_text())
        assert "elapsed_seconds" not in raw


def test_queued_session_is_recorded_when_lane_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a"):
        lane.acquire("sess-b", "/wt/b")
        assert [e["session"] for e in lane.status()["waiting"]] == ["sess-b"]


def test_release_returns_the_next_waiter(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    a = lane.acquire("sess-a", "/wt/a")
    lane.acquire("sess-b", "/wt/b")
    assert a.release() == "sess-b"
    # The hand-off target survives the block, which is what `lane run` reports.
    assert a.next_session == "sess-b"


def test_release_returns_none_when_queue_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    assert lane.acquire("sess-a", "/wt/a").release() is None


def test_releasing_frees_the_lane_for_the_next_acquirer(tmp_path, monkeypatch):
    # The kernel lock must be dropped, not just the record: a released lane that
    # still holds LOCK_EX would refuse every future acquirer with no way back.
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    lane.acquire("sess-a", "/wt/a").release()
    assert lane.status()["holder"] is None
    with lane.acquire("sess-b", "/wt/b") as held:
        assert held is not None


def test_departed_holders_record_is_reclaimable_and_its_pid_is_ignored(tmp_path, monkeypatch):
    """A record with no kernel lock behind it is a holder that has departed.

    The pid written into it is display only. 4194304 is this machine's
    `/proc/sys/kernel/pid_max` (measured, not merely assumed to be above it) —
    pids are allocated strictly *below* pid_max, so the value can never name a
    live process. It is seeded here precisely to prove that nothing keys on it:
    liveness comes from the absence of a flock, and reclaim happens whatever
    the pid says.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    (tmp_path / lane.LOCK).write_text(json.dumps(
        {"session": "ghost", "worktree": "/wt/ghost", "pid": 4194304, "acquired_at": time.time()}
    ))
    with lane.acquire("sess-b", "/wt/b") as held:
        assert held is not None


def test_a_departed_holders_record_is_reclaimable_even_when_its_pid_is_live(
        tmp_path, monkeypatch):
    """The one property a flock has and `os.kill(pid, 0)` cannot: a RECYCLED pid.

    The seeded record names a pid that is genuinely alive — this test process —
    with no flock behind it, which is exactly what a crashed holder leaves once
    the kernel has handed its number to somebody else. Under pid liveness the
    probe succeeds, the lane reads as held by a process that is not the holder,
    and there is no timeout to break it: wedged until a human intervenes. Under
    the kernel lock it is reclaimed at once, because the signal is the lock and
    never the number.

    This is the discriminator the rest of the file lacks. Verified both ways:
    with `acquire()` patched back to `os.kill(pid, 0)` liveness this test fails
    while every other test in the file still passes — so nothing else here can
    tell the two designs apart.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    (tmp_path / lane.LOCK).write_text(json.dumps(
        {"session": "ghost", "worktree": "/wt/ghost",
         "pid": os.getpid(), "acquired_at": time.time()}
    ))
    with lane.acquire("sess-b", "/wt/b") as held:
        assert held is not None
        assert lane.status()["holder"]["session"] == "sess-b"


def test_live_holder_is_never_reclaimed_at_any_age(tmp_path, monkeypatch):
    # There is no timeout by design. An hour-old live holder still holds.
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a"):
        p = tmp_path / lane.LOCK
        d = json.loads(p.read_text()); d["acquired_at"] = 0; p.write_text(json.dumps(d))
        assert lane.acquire("sess-b", "/wt/b") is None


def test_reacquiring_your_own_lane_succeeds_instead_of_queueing(tmp_path, monkeypatch):
    """The holder must recognise itself.

    A session retrying after a transient error would otherwise enqueue behind
    itself and wait for a hand-off only its own release can send — and there is
    no timeout to break that, so the lane wedges until a human clears it.

    This asserts the LIBRARY property and nothing more. Whether the *caller* may
    then go on to run a second bring-up is a separate question, and the answer is
    no — see `test_lane_run_refuses_a_second_bringup_under_its_own_live_hold`.
    Asserting only the handle here is what let `_cmd_run` run one anyway.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    with lane.acquire("sess-a", "/wt/a") as outer:
        again = lane.acquire("sess-a", "/wt/a")
        assert again is not None
        assert again.reentrant is True
        assert lane.status()["waiting"] == [], "must not queue behind itself"
        # The re-entrant handle took no kernel lock, so it must free nothing:
        # releasing here would drop the lane out from under the outer bring-up.
        assert again.release() is None
        assert lane.status()["holder"]["session"] == "sess-a"
        assert lane.acquire("sess-b", "/wt/b") is None, "outer hold must survive"
    assert outer.next_session == "sess-b"


def test_release_raises_when_lane_is_not_held(tmp_path, monkeypatch):
    """`if held and held["session"] != session` is falsy when held is None,
    so a naive implementation falls through and pops a real queue entry on
    behalf of a session that never held the lane — double-advancing the queue
    past a waiter that is never released to. release() must refuse instead."""
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    a = lane.acquire("sess-a", "/wt/a")
    lane.acquire("sess-b", "/wt/b")     # queued behind sess-a
    lane.acquire("sess-c", "/wt/c")     # and sess-c behind that
    assert a.release() == "sess-b"      # lane now free; sess-b popped
    with pytest.raises(RuntimeError):
        a.release()                     # sess-a holds nothing anymore — must raise
    assert [e["session"] for e in lane.status()["waiting"]] == ["sess-c"], \
        "the refused release must not have advanced the queue past sess-c"


def test_drop_and_next_skips_the_unreachable_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    a = lane.acquire("sess-a", "/wt/a")
    lane.acquire("sess-b", "/wt/b")
    lane.acquire("sess-c", "/wt/c")
    assert a.release() == "sess-b"
    assert lane.drop_and_next("sess-b") == "sess-c"
    assert [e["session"] for e in lane.status()["waiting"]] == ["sess-c"]


# --- Liveness is the kernel lock, not a recorded pid ----------------------
#
# The original design recorded os.getpid() and reclaimed when os.kill(pid, 0)
# failed. A session invokes Python as a SHORT-LIVED SUBPROCESS, so that pid is
# dead the moment it is written: the next acquire() would reclaim a lane whose
# bring-up is still running, and both sessions would proceed reporting success.
# These tests pin the replacement across real processes — nothing here
# simulates a death by editing a pid into a file.


def _hold_until_killed(lane_dir_str, session, ready):
    """Acquire in a child process and keep holding until the parent kills us."""
    lane_mod = importlib.import_module("lane")
    lane_mod.lane_dir = lambda: Path(lane_dir_str)
    held = lane_mod.acquire(session, "/wt/holder")
    ready.put(held is not None)
    time.sleep(300)   # the parent kills this process; it never returns


def test_a_running_holder_is_never_reclaimed_and_a_gone_one_always_is(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    ctx = mp.get_context("fork")
    ready = ctx.Queue()
    child = ctx.Process(target=_hold_until_killed, args=(str(tmp_path), "sess-holder", ready))
    child.start()
    try:
        assert ready.get(timeout=15) is True
        recorded_pid = lane.status()["holder"]["pid"]
        assert recorded_pid == child.pid

        # Still running: the lane must NOT be reclaimable, at any age.
        assert lane.acquire("sess-b", "/wt/b") is None

        # SIGKILL, so nothing in the holder runs on the way out — no cleanup, no
        # release, exactly what a crashed or rebooted session looks like.
        child.kill()
        child.join(timeout=15)
        assert not child.is_alive()
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=15)

    # The record still names the dead holder — which is why nothing may key on
    # it — but the kernel dropped the flock at process teardown, so the lane is
    # free and the next acquirer takes it.
    assert lane.status()["holder"]["session"] == "sess-holder"
    with lane.acquire("sess-c", "/wt/c") as got:
        assert got is not None
        assert lane.status()["holder"]["session"] == "sess-c"


def test_lane_run_queues_instead_of_running_while_another_process_holds(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    ran = tmp_path / "ran"
    monkeypatch.setattr(lane, "load_config",
                        lambda checkout=None: {"bringup": f"touch {ran}", "validate": None, "teardown": None})
    ctx = mp.get_context("fork")
    ready = ctx.Queue()
    child = ctx.Process(target=_hold_until_killed, args=(str(tmp_path), "sess-holder", ready))
    child.start()
    try:
        assert ready.get(timeout=15) is True
        assert lane._cmd_run("sess-b", str(tmp_path)) == lane.EXIT_QUEUED
    finally:
        child.kill()
        child.join(timeout=15)
    assert not ran.exists(), "bringup ran while another process held the lane"
    assert "STOP HERE" in capsys.readouterr().out
    assert [e["session"] for e in lane.status()["waiting"]] == ["sess-b"]


def test_lane_run_refuses_a_second_bringup_under_its_own_live_hold(
        tmp_path, monkeypatch, capsys):
    """Reentrancy must stop the caller, not wave it through.

    Liveness is a kernel lock, so `acquire()` can only report reentrancy while a
    LIVE process of the same session holds the lane. That makes this branch, by
    construction, "a second bring-up on top of a running one" — the interleave
    again, one level up, inside the fix for it. Reachable without any exotic
    timing: a `lane run --session S` tool call times out at two minutes while
    the bring-up is still going and the caller re-issues the same command,
    which is the retry the reentrant branch was written to accommodate.

    The child here is a real second process holding under the SAME session name,
    so this is the reachable shape and not an in-process simulation of it.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    ran = tmp_path / "ran"
    monkeypatch.setattr(lane, "load_config",
                        lambda checkout=None: {"bringup": f"touch {ran}", "validate": None, "teardown": None})
    ctx = mp.get_context("fork")
    ready = ctx.Queue()
    child = ctx.Process(target=_hold_until_killed, args=(str(tmp_path), "sess-a", ready))
    child.start()
    try:
        assert ready.get(timeout=15) is True
        assert lane._cmd_run("sess-a", str(tmp_path)) == lane.EXIT_ALREADY_RUNNING
        assert not ran.exists(), "a second bring-up started on top of a running one"
        # Distinct from EXIT_QUEUED because the caller is NOT queued: nothing will
        # ever push it, so telling it to wait would be telling it to wait forever.
        assert lane.EXIT_ALREADY_RUNNING != lane.EXIT_QUEUED
        assert lane.status()["waiting"] == [], "must not queue behind its own hold"
        # Refusing must not free the lane out from under the run that is still going.
        holder = lane.status()["holder"]
        assert holder["session"] == "sess-a" and holder["pid"] == child.pid
        assert lane.acquire("sess-b", "/wt/b") is None, "the live hold must survive"
        assert "REFUSING" in capsys.readouterr().out
    finally:
        child.kill()
        child.join(timeout=15)


# --- `lane run` holds the lane for the whole run -------------------------

_PROBE = """\
import json, sys
from pathlib import Path
sys.path.insert(0, {scripts!r})
import lane
lane.lane_dir = lambda: Path({lane_dir!r})
held = lane.acquire("sess-probe", "/wt/probe")
Path({out!r}).write_text(json.dumps({{"acquired": held is not None}}))
"""


def test_lane_run_holds_the_lane_across_bringup_and_validate(tmp_path, monkeypatch, capsys):
    """The wiring the pid design was missing: one process that holds for the whole run.

    A separate OS process probes the lane from inside the bringup step — the
    window in which the old design had already let go, because the interpreter
    that recorded the pid had exited. The probe must be refused, and the lane
    must be free again once `run` returns.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    probe_py = tmp_path / "probe.py"
    out = tmp_path / "probe.json"
    probe_py.write_text(_PROBE.format(
        scripts=str(ROOT / "scripts"), lane_dir=str(tmp_path), out=str(out)))
    monkeypatch.setattr(lane, "load_config", lambda checkout=None: {
        "bringup": f"{sys.executable} {probe_py}",
        "validate": f"test -f {out}",   # runs only if bringup succeeded
        "teardown": None,
    })

    assert lane._cmd_run("sess-run", str(tmp_path)) == 0
    assert json.loads(out.read_text()) == {"acquired": False}, \
        "a concurrent process acquired the lane while `run` was mid-bringup"
    assert lane.status()["holder"] is None, "`run` must release when it finishes"
    # The probe enqueued itself, so `run` must name it as the hand-off target.
    assert "HAND OFF TO: sess-probe" in capsys.readouterr().out


def test_lane_run_reads_the_config_from_the_checkout_it_runs_in(tmp_path, monkeypatch):
    """One answer to "which checkout", not two that can disagree.

    `--worktree X` used to run the steps in X while reading the config from
    whatever checkout the caller happened to stand in, so a session could be
    validated with a command from a different branch. The stub asserts the
    argument rather than accepting any, because a double that ignores what it
    is passed cannot tell a threaded value from a dropped one.
    """
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    seen = {}

    def fake_load_config(checkout=None):
        seen["checkout"] = checkout
        return {"bringup": None, "validate": None, "teardown": None}

    monkeypatch.setattr(lane, "load_config", fake_load_config)
    assert lane._cmd_run("sess-a", "/wt/somewhere") == 0
    assert seen["checkout"] == "/wt/somewhere"


def test_lane_run_reports_failure_but_still_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "lane_dir", lambda: tmp_path)
    monkeypatch.setattr(lane, "load_config",
                        lambda checkout=None: {"bringup": "false", "validate": "true", "teardown": None})
    assert lane._cmd_run("sess-run", str(tmp_path)) == 1
    # A failed bring-up must not strand the lane — the next session still needs it.
    assert lane.status()["holder"] is None


# --- Receipts: evidence that a commit was validated ----------------------
#
# A later step merges a pull request once its checks are green, and what makes
# that safe is a rule that nothing merges without having passed a lane run
# first. The receipt is the evidence for that rule, which is why a receipt that
# attests to nothing is worse than no receipt at all: it reads exactly like one
# that does. Three shapes of meaningless receipt are pinned below — one for a
# validation that was skipped, one for a validation that failed, and one naming
# a commit nothing ran against.
#
# Real repositories throughout. The property under test is that the receipt
# names a commit that exists in the checkout that was validated, so a stubbed
# sha would assert only that the stub was written correctly.


def _repo_with_config(path: Path, **steps) -> Path:
    """A real repository carrying a committed config the lane will read."""
    repo = _new_repo(path)
    cfg = {"bringup": None, "validate": None,
           "default_branch": "main", "required_check": "ci"}
    cfg.update(steps)
    (repo / lane_config.CONFIG_NAME).write_text(json.dumps(cfg))
    _git("add", lane_config.CONFIG_NAME, cwd=repo)
    _git("commit", "-q", "-m", "configure the lane", cwd=repo)
    return repo


def _receipts(project: Path) -> list[str]:
    """Every receipt on disk, read WITHOUT creating the directory.

    Deliberately not routed through `lane.receipts_dir()`, which creates what
    it returns: a negative assertion made through it cannot tell "nothing was
    written" from "the directory did not exist until this assertion made it".
    The literal layout is spelled out rather than derived for the same reason
    the merge step will read it literally — it is the contract, not an
    implementation detail.
    """
    return sorted(p.name for p in (project / ".pitcall" / "receipts").glob("*.json"))


def test_a_passing_validate_records_the_commit_of_the_checkout_it_ran_in(
        tmp_path, monkeypatch):
    """The receipt is pinned to the sha `validate` actually ran against.

    The caller's cwd and the checkout under test are different directories —
    `lane run --worktree X` is the ordinary case, not the exotic one — and this
    module has twice resolved the wrong one of that pair. So the run is issued
    from the main checkout while the worktree under test sits on a later commit,
    and the receipt must name the worktree's HEAD while saying nothing whatever
    about the caller's.
    """
    project = _repo_with_config(tmp_path / "project", bringup="true", validate="true")
    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "side", cwd=project)
    linked = linked.resolve()
    (linked / "fix.txt").write_text("a review fix, landed after validation\n")
    _git("add", "fix.txt", cwd=linked)
    _git("commit", "-q", "-m", "a later commit", cwd=linked)

    callers_head = _git("rev-parse", "HEAD", cwd=project)
    validated_head = _git("rev-parse", "HEAD", cwd=linked)
    assert callers_head != validated_head, \
        "the two checkouts must sit on different commits, or this proves nothing"

    monkeypatch.chdir(project)
    assert lane._cmd_run("sess-a", str(linked)) == 0

    # Exactly one receipt, and it is the validated worktree's commit. Naming
    # the absent one too: "a receipt exists" would pass on the wrong sha.
    assert _receipts(project) == [f"{validated_head}.json"]
    assert not (project / ".pitcall" / "receipts" / f"{callers_head}.json").exists()

    receipt = project / ".pitcall" / "receipts" / f"{validated_head}.json"
    assert json.loads(receipt.read_text())["sha"] == validated_head

    # One set of receipts per project, for the same reason there is one lock:
    # a merge step consulting a per-worktree directory would be consulting
    # receipts nobody else ever wrote to.
    assert lane.receipts_dir() == project / ".pitcall" / "receipts"
    monkeypatch.chdir(linked)
    assert lane.receipts_dir() == project / ".pitcall" / "receipts"


def test_a_failing_validate_leaves_no_receipt(tmp_path, monkeypatch):
    """A receipt for a validation that FAILED reads identically to one for a
    validation that passed, and the merge it would authorise is a merge of code
    that is known not to work."""
    project = _repo_with_config(tmp_path / "project", bringup="true", validate="false")
    monkeypatch.chdir(project)

    assert lane._cmd_run("sess-a", str(project)) == 1
    assert _receipts(project) == []


def test_a_failed_bringup_leaves_no_receipt_because_validate_never_ran(
        tmp_path, monkeypatch):
    """The lane stops at the first failing step, so `validate` is never
    reached — and a receipt written anyway would attest to a step that did not
    execute at all."""
    marker = tmp_path / "validate-ran"
    project = _repo_with_config(tmp_path / "project",
                                bringup="false", validate=f"touch {marker}")
    monkeypatch.chdir(project)

    assert lane._cmd_run("sess-a", str(project)) == 1
    assert not marker.exists(), \
        "validate ran after a failed bringup — this test is not testing what it says"
    assert _receipts(project) == []


def test_an_unconfigured_validate_leaves_no_receipt(tmp_path, monkeypatch):
    """The skipped-step case, and the one an implementation is likeliest to get
    wrong.

    `validate` may be null — a project saying "there is no such step" — and the
    lane already skips a step configured that way. The run therefore exits 0
    having validated nothing, so a receipt keyed on the run's exit status is
    written here, is indistinguishable from a real one, and authorises a merge
    nothing ever checked.
    """
    project = _repo_with_config(tmp_path / "project", bringup="true", validate=None)
    monkeypatch.chdir(project)

    assert lane._cmd_run("sess-a", str(project)) == 0, \
        "a skipped step is not a failure — the run still succeeds"
    assert _receipts(project) == []


def test_two_runs_at_different_commits_leave_two_receipts(tmp_path, monkeypatch):
    """The ordinary failure the sha-keyed name prevents: validation runs, a
    review fix lands, and the fix inherits the earlier approval without ever
    having been validated. Each commit carries its own receipt and neither run
    overwrites the other's."""
    project = _repo_with_config(tmp_path / "project", bringup="true", validate="true")
    monkeypatch.chdir(project)

    assert lane._cmd_run("sess-a", str(project)) == 0
    before = _git("rev-parse", "HEAD", cwd=project)

    (project / "fix.txt").write_text("a review fix\n")
    _git("add", "fix.txt", cwd=project)
    _git("commit", "-q", "-m", "the review fix", cwd=project)
    after = _git("rev-parse", "HEAD", cwd=project)
    assert before != after, "the second run must be at a different commit"

    assert lane._cmd_run("sess-b", str(project)) == 0

    assert _receipts(project) == sorted([f"{before}.json", f"{after}.json"])
    receipts = project / ".pitcall" / "receipts"
    assert json.loads((receipts / f"{before}.json").read_text())["sha"] == before
    assert json.loads((receipts / f"{after}.json").read_text())["sha"] == after


# --- Real concurrency ----------------------------------------------------
#
# Everything above is single-process: monkeypatching lane_dir and calling
# acquire()/release() sequentially in the test's own interpreter. That proves
# the state machine is *logically* correct but cannot exercise an interleaving
# — a naive "read, decide, write" implementation passes every test above
# identically to the fcntl-guarded one. These tests use real OS processes
# (multiprocessing, fork context — Linux only, matching this module's target)
# racing against the same lane directory, with a Barrier to line every racer
# up at the same instant and maximise the chance of catching a real interleave.


def _acquire_worker(lane_dir_str, session, worktree, results, barrier, done):
    """Run in a forked child: point this process's `lane` module at the shared
    lane dir, wait for every sibling to be ready, then race acquire()."""
    lane_mod = importlib.import_module("lane")
    lane_mod.lane_dir = lambda: Path(lane_dir_str)
    try:
        barrier.wait(timeout=10)
        held = lane_mod.acquire(session, worktree)
        results.put((session, held is not None, None))
    except Exception as exc:  # surface any crash instead of hanging the join
        results.put((session, False, repr(exc)))
    # Hold until the parent has read every outcome. A winner that exited here
    # would drop its kernel lock and let a straggler win the lane too — a real
    # property of the design (the lock dies with the process), but not the
    # interleaving under test.
    done.wait(timeout=20)


def _race_acquire(d: Path, n: int, seed: dict | None) -> list[tuple[str, bool, str | None]]:
    """Launch n processes racing acquire() against a fresh lane dir `d`.

    `seed`, if given, is written as lane.lock's contents before the race starts
    (used to seed a departed holder's record for the crashed-holder race).
    """
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if seed is not None:
        (d / lane.LOCK).write_text(json.dumps(seed))
    ctx = mp.get_context("fork")
    results = ctx.Queue()
    barrier = ctx.Barrier(n)
    done = ctx.Event()
    procs = [
        ctx.Process(target=_acquire_worker,
                    args=(str(d), f"sess-{i}", f"/wt/{i}", results, barrier, done))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    try:
        outcomes = [results.get(timeout=15) for _ in range(n)]
    finally:
        done.set()
    for p in procs:
        p.join(timeout=15)
    return outcomes


def test_concurrent_acquire_on_free_lane_exactly_one_winner(tmp_path):
    """N processes race acquire() on a lane nobody holds. Baseline invariant:
    the kernel resolves concurrent LOCK_EX|LOCK_NB requests on one inode, so at
    most one racer can come away holding it — this establishes the base property
    the other two tests build on."""
    for round_ in range(20):
        outcomes = _race_acquire(tmp_path / f"free-{round_}", n=6, seed=None)
        errors = [e for _, _, e in outcomes if e]
        assert not errors, f"round {round_}: worker errors {errors}"
        winners = [s for s, ok, _ in outcomes if ok]
        assert len(winners) == 1, f"round {round_}: winners={winners} outcomes={outcomes}"


def test_concurrent_acquire_against_departed_holder_exactly_one_winner(tmp_path):
    """Seed the record a crashed holder leaves behind — contents naming a
    holder, with no kernel lock behind them — then race N processes at it. The
    naive implementation read-decides-unlinks-recreates with no guard between
    the read and the unlink, so two racers could both observe the holder as gone
    and both end up believing they hold the lane. Reproduced by the reviewer
    with real multiprocessing: 48/200 four-way trials produced more than one
    winner. Exactly one winner must come out of every round, every time."""
    # 4194304 is this machine's pid_max itself (measured), and pids are allocated
    # strictly below it — so it can never be live. It is seeded only to show that
    # the reclaim decision ignores it: the flock is the signal.
    departed_pid = 4194304
    for round_ in range(20):
        seed = {"session": "ghost", "worktree": "/wt/ghost",
                "pid": departed_pid, "acquired_at": time.time()}
        outcomes = _race_acquire(tmp_path / f"departed-{round_}", n=6, seed=seed)
        errors = [e for _, _, e in outcomes if e]
        assert not errors, f"round {round_}: worker errors {errors}"
        winners = [s for s, ok, _ in outcomes if ok]
        assert len(winners) == 1, f"round {round_}: winners={winners} outcomes={outcomes}"


def test_concurrent_enqueue_behind_held_lane_all_entries_survive(tmp_path, monkeypatch):
    """The lane is genuinely held (by this test process, which keeps the
    kernel lock for the whole round, so every racer takes the enqueue path and
    never the acquire path). N processes concurrently read-modify-write the
    queue file. The naive implementation has no lock around that RMW and writes
    with plain write_text (not atomic), so concurrent writers clobber each
    other's read of the queue and can also interleave two writes into one
    corrupt file. Reproduced by the reviewer: 25/30 two-way enqueues lost an
    entry outright, 5/30 produced unparseable JSON — which lane._read()
    silently turns into None, discarding the *entire* queue. This test parses
    the raw file directly with json.loads (not lane._read) so a torn write
    raises JSONDecodeError instead of silently reading back as "empty queue"."""
    n = 6
    for round_ in range(15):
        d = tmp_path / f"enqueue-{round_}"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        monkeypatch.setattr(lane, "lane_dir", lambda d=d: d)
        # Hold the lane from THIS process and keep holding for the whole round.
        # The hold is a kernel lock, so it is real for every racer below rather
        # than a claim written into a file.
        holder = lane.acquire("holder", "/wt/holder")
        assert holder is not None
        ctx = mp.get_context("fork")
        results = ctx.Queue()
        barrier = ctx.Barrier(n)
        done = ctx.Event()
        procs = [
            ctx.Process(target=_acquire_worker,
                        args=(str(d), f"sess-{i}", f"/wt/{i}", results, barrier, done))
            for i in range(n)
        ]
        for p in procs:
            p.start()
        try:
            outcomes = [results.get(timeout=15) for _ in range(n)]
        finally:
            done.set()
        for p in procs:
            p.join(timeout=15)

        errors = [e for _, _, e in outcomes if e]
        assert not errors, f"round {round_}: worker errors {errors}"
        # The lane is held throughout — nobody should have acquired it.
        assert all(ok is False for _, ok, _ in outcomes), f"round {round_}: {outcomes}"

        raw_text = (d / lane.QUEUE).read_text()
        q = json.loads(raw_text)  # raises JSONDecodeError on a torn/corrupt write
        got = {e["session"] for e in q}
        want = {f"sess-{i}" for i in range(n)}
        assert got == want, f"round {round_}: lost entries — got {got}, want {want}"
        holder.release()
