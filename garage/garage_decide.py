"""The garage's scheduling decisions, as pure functions.

Poll results and runner counts in, decisions out: no clock, no threads, no
I/O, so every case runs in a test without sleeping. The service (Task 13)
owns the state these read.
"""

from __future__ import annotations

from dataclasses import dataclass


def label_key(labels) -> frozenset[str]:
    """A label set compared the way decision 14 says: case-insensitive, order ignored."""
    return frozenset(label.lower() for label in labels)


def confirmed_set(key: frozenset, label_sets: list[list[str]]) -> list[str] | None:
    """The operator's own spelling of the confirmed set with this key."""
    for labels in label_sets:
        if label_key(labels) == key:
            return labels
    return None


def wanted(jobs: list[dict], label_sets: list[list[str]]) -> list[dict]:
    keys = {label_key(labels) for labels in label_sets}
    return [job for job in jobs
            if job.get("status") == "queued" and label_key(job.get("labels") or []) in keys]


@dataclass
class Demand:
    repo: str
    key: frozenset
    count: int
    oldest: str


def demand(wanted_by_repo: dict[str, list[dict]],
           pending: dict[tuple[str, frozenset], int]) -> dict[str, list[Demand]]:
    """Wanted jobs per repo and label set, minus runners already handed out for that set.

    Without the subtraction one queued job would get a new runner on every poll
    until it starts.
    """
    result: dict[str, list[Demand]] = {}
    for repo, jobs in wanted_by_repo.items():
        groups: dict[frozenset, Demand] = {}
        for job in jobs:
            key = label_key(job["labels"])
            found = groups.setdefault(key, Demand(repo, key, 0, job["created_at"]))
            found.count += 1
            found.oldest = min(found.oldest, job["created_at"])
        kept = []
        for found in groups.values():
            found.count -= pending.get((repo, found.key), 0)
            if found.count > 0:
                kept.append(found)
        if kept:
            result[repo] = kept
    return result


def hand_out(free: list[str], demand_by_repo: dict[str, list[Demand]], repos: list[str],
             cursor: int) -> tuple[list[tuple[str, str, frozenset]], int]:
    """Give free runners to repos in turn, starting at `cursor`; one per repo per pass.

    Within a repo the set whose oldest wanted job has waited longest goes first.
    Demand for a repo not in `repos` is ignored, so the loop always ends.
    """
    if not repos:
        return [], cursor
    left = {repo: [Demand(d.repo, d.key, d.count, d.oldest) for d in demand_by_repo[repo]]
            for repo in repos if repo in demand_by_repo}
    runners = list(free)
    given: list[tuple[str, str, frozenset]] = []
    i = cursor % len(repos)
    while runners and any(d.count > 0 for ds in left.values() for d in ds):
        repo = repos[i]
        i = (i + 1) % len(repos)
        live = [d for d in left.get(repo, []) if d.count > 0]
        if not live:
            continue
        pick = min(live, key=lambda d: d.oldest)
        pick.count -= 1
        given.append((runners.pop(0), repo, pick.key))
        cursor = i
    return given, cursor


def backoff_seconds(failures: int) -> int:
    """1, 2, 4, 8, 16, then 30 minutes for every later failure."""
    return 60 * min(2 ** (min(max(failures, 1), 6) - 1), 30)
