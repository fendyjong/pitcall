"""The garage's pure scheduling decisions (Task 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
from garage_decide import (Demand, backoff_seconds, confirmed_set, demand,  # noqa: E402
                           hand_out, label_key, wanted)

LINUX = ["self-hosted", "linux"]
LARGE = ["self-hosted", "linux", "large"]
K_LINUX = label_key(LINUX)
K_LARGE = label_key(LARGE)


def job(n, labels, created, status="queued"):
    return {"id": n, "status": status, "labels": labels, "created_at": created}


def test_label_sets_match_ignoring_case_and_order():
    assert label_key(["Linux", "Self-Hosted"]) == K_LINUX
    assert confirmed_set(label_key(["LINUX", "self-hosted"]), [LARGE, LINUX]) is LINUX
    assert confirmed_set(label_key(["self-hosted"]), [LINUX]) is None


def test_only_queued_jobs_on_a_confirmed_set_are_wanted():
    jobs = [
        job(1, ["linux", "self-hosted"], "t1"),
        job(2, ["ubuntu-latest"], "t2"),                      # GitHub-hosted: never served
        job(3, ["self-hosted"], "t3"),                         # a subset is not equal
        job(4, ["self-hosted", "linux", "large", "x"], "t4"),  # nor is a superset
        job(5, LINUX, "t5", status="in_progress"),
    ]
    assert [j["id"] for j in wanted(jobs, [LINUX, LARGE])] == [1]


def test_demand_subtracts_runners_already_handed_out():
    by_repo = {"octo/app": [job(1, LINUX, "2026-11-14T10:00:00Z"),
                            job(2, LINUX, "2026-11-14T09:00:00Z"),
                            job(3, LARGE, "2026-11-14T11:00:00Z")]}
    assert demand(by_repo, {}) == {"octo/app": [
        Demand("octo/app", K_LINUX, 2, "2026-11-14T09:00:00Z"),
        Demand("octo/app", K_LARGE, 1, "2026-11-14T11:00:00Z")]}
    assert demand(by_repo, {("octo/app", K_LINUX): 2}) == {"octo/app": [
        Demand("octo/app", K_LARGE, 1, "2026-11-14T11:00:00Z")]}


def test_one_queued_job_gets_one_runner_not_one_per_poll():
    by_repo = {"octo/app": [job(1, LINUX, "t")]}
    first, _ = hand_out(["r1", "r2"], demand(by_repo, {}), ["octo/app"], 0)
    assert first == [("r1", "octo/app", K_LINUX)]
    # Next poll: the job is still queued, r1 is still starting -> nothing more.
    again, _ = hand_out(["r2"], demand(by_repo, {("octo/app", K_LINUX): 1}), ["octo/app"], 0)
    assert again == []


def test_repos_take_turns_from_the_cursor():
    d = {"a/a": [Demand("a/a", K_LINUX, 4, "t1")], "b/b": [Demand("b/b", K_LINUX, 1, "t9")]}
    given, cursor = hand_out(["r1", "r2", "r3"], d, ["a/a", "b/b"], 0)
    assert given == [("r1", "a/a", K_LINUX), ("r2", "b/b", K_LINUX), ("r3", "a/a", K_LINUX)]
    assert cursor == 1
    given, cursor = hand_out(["r4"], d, ["a/a", "b/b"], 1)
    assert given == [("r4", "b/b", K_LINUX)]
    assert cursor == 0


def test_within_a_repo_the_longest_waiting_set_goes_first():
    d = {"a/a": [Demand("a/a", K_LINUX, 1, "2026-11-14T10:00:00Z"),
                 Demand("a/a", K_LARGE, 1, "2026-11-14T08:00:00Z")]}
    given, _ = hand_out(["r1", "r2"], d, ["a/a"], 0)
    assert given == [("r1", "a/a", K_LARGE), ("r2", "a/a", K_LINUX)]


def test_hand_out_stops_when_runners_or_demand_run_out_and_does_not_mutate_input():
    d = {"a/a": [Demand("a/a", K_LINUX, 1, "t")], "gone/repo": [Demand("gone/repo", K_LINUX, 3, "t")]}
    given, cursor = hand_out(["r1", "r2"], d, ["a/a"], 5)
    assert given == [("r1", "a/a", K_LINUX)]
    assert d["a/a"][0].count == 1
    assert hand_out([], d, ["a/a"], 0) == ([], 0)
    assert hand_out(["r1"], d, [], 3) == ([], 3)


def test_backoff_doubles_from_one_minute_and_caps_at_thirty():
    assert [backoff_seconds(n) // 60 for n in range(1, 9)] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert backoff_seconds(1000) == 1800


def test_demand_drops_a_set_its_pending_runners_already_cover_or_exceed():
    by_repo = {"octo/app": [job(1, LINUX, "t1")], "octo/site": [job(2, LINUX, "t2")]}
    assert demand(by_repo, {("octo/app", K_LINUX): 3, ("octo/site", K_LINUX): 1}) == {}
