"""`wdd-ledger` — the one reader of ledger lines and full-branch review verdicts.

Every rule is tested in both directions: that it fires, and that it stays
quiet on the input it must not fire on. A gate tested only for firing is how
#10 shipped — `BLOCKED` matched anywhere, so prose describing a gate refused a
clean ledger.
"""

import re
from pathlib import Path

import pytest

from conftest import run_script

HEADER = "# WDD ledger — plan: docs/plans/2026-01-01-demo.md\n"


def ledger(tmp_path: Path, *lines: str, whole: bool = True) -> Path:
    p = tmp_path / "progress.md"
    p.write_text(HEADER + "\n".join(lines) + ("\n" if whole else ""), encoding="utf-8")
    return p


def ledger_cmd(tmp_path, *args):
    return run_script("wdd-ledger", *args, cwd=tmp_path)


# --- stops ------------------------------------------------------------------


def test_a_task_stop_marker_refuses_and_is_printed(tmp_path):
    r = ledger_cmd(tmp_path, "stops", str(ledger(tmp_path, "Task 2: BLOCKED")))
    assert r.returncode == 1
    assert r.stdout.strip() == "Task 2: BLOCKED"


def test_a_final_review_stop_marker_refuses(tmp_path):
    r = ledger_cmd(tmp_path, "stops",
                   str(ledger(tmp_path, "Final review #3: BLOCKED at abc1234")))
    assert r.returncode == 1
    assert "Final review #3: BLOCKED" in r.stdout


def test_blocked_in_prose_is_not_a_stop_marker(tmp_path):
    """#10: the word inside a table row, a ruling, or a note is prose."""
    p = ledger(
        tmp_path,
        "| T2 x T4 | ... | T4 Step 1 reports BLOCKED rather than proceeding |",
        "Ruling: the implementer should report BLOCKED — x — y",
        "Note: a gate that reports BLOCKED in prose — low value",
        "Note: Task 2: BLOCKED is how a stop is recorded — gate",
        "Ruling: Final review #3: BLOCKED means stop — x — y",
        "| T2 | Task 2: BLOCKED | quoted |",
    )
    r = ledger_cmd(tmp_path, "stops", str(p))
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_later_complete_line_resolves_a_task_stop(tmp_path):
    p = ledger(tmp_path, "Task 2: BLOCKED",
               "Task 2: complete (commits a..b, review clean, branch x-t2)")
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 0


def test_a_complete_line_for_another_task_does_not_resolve_it(tmp_path):
    p = ledger(tmp_path, "Task 2: BLOCKED",
               "Task 3: complete (commits a..b, review clean, branch x-t3)")
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 1


def test_a_complete_line_before_the_stop_does_not_resolve_it(tmp_path):
    p = ledger(tmp_path, "Task 2: complete (commits a..b, review clean, branch x-t2)",
               "Task 2: BLOCKED")
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 1


def test_a_later_review_resolves_a_review_stop(tmp_path):
    p = ledger(tmp_path, "Final review #3: BLOCKED at abc1234",
               "Final review #4: CLEAN at def5678")
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 0


def test_an_earlier_numbered_review_does_not_resolve_a_review_stop(tmp_path):
    p = ledger(tmp_path, "Final review #3: BLOCKED at abc1234",
               "Final review #2: CLEAN at def5678")
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 1


@pytest.mark.parametrize("marker", ["Task 2: BLOCKED", "Final review #3: BLOCKED at abc1234"])
def test_an_unterminated_stop_marker_still_stops(tmp_path, marker):
    """A crash mid-append leaves a fragment. It may RAISE a stop: failing closed
    on half a marker costs one look at the ledger, and failing open ships past it."""
    r = ledger_cmd(tmp_path, "stops", str(ledger(tmp_path, marker, whole=False)))
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout.strip() == marker


def test_an_unterminated_complete_line_does_not_resolve_a_stop(tmp_path):
    """...and never RESOLVE one: half a `complete` line is not a finished task."""
    p = ledger(tmp_path, "Task 2: BLOCKED",
               "Task 2: complete (commits a..b, review clean, branch x-t2)", whole=False)
    assert ledger_cmd(tmp_path, "stops", str(p)).returncode == 1


# --- sweep-guard --------------------------------------------------------------


def test_no_sweep_plan_means_no_guard(tmp_path):
    p = ledger(tmp_path, "Wave 7: dispatched (BASE a, T1 @ x-t1 [cheapest/haiku])")
    assert ledger_cmd(tmp_path, "sweep-guard", str(p)).returncode == 0


def test_waves_inside_the_sweep_range_pass(tmp_path):
    p = ledger(tmp_path, "Wave 1: dispatched (BASE a)", "Sweep plan: waves 2-3",
               "Wave 2: dispatched (BASE b)", "Wave 3: dispatched (BASE c)")
    assert ledger_cmd(tmp_path, "sweep-guard", str(p)).returncode == 0


def test_a_wave_after_the_sweep_range_refuses(tmp_path):
    p = ledger(tmp_path, "Sweep plan: waves 3-4", "Wave 5: dispatched (BASE a)")
    r = ledger_cmd(tmp_path, "sweep-guard", str(p))
    assert r.returncode == 1
    assert "wave 5" in r.stderr


def test_a_second_sweep_plan_refuses(tmp_path):
    p = ledger(tmp_path, "Sweep plan: waves 2-2", "Sweep plan: waves 3-3")
    r = ledger_cmd(tmp_path, "sweep-guard", str(p))
    assert r.returncode == 1
    assert "one sweep pass" in r.stderr


@pytest.mark.parametrize("plan", ["Sweep plan: waves 2–2", "Sweep plan: waves 3 - 4"])
def test_a_sweep_plan_that_does_not_parse_refuses(tmp_path, plan):
    """Fail closed: a plan line the guard cannot read must not switch the guard off."""
    p = ledger(tmp_path, plan, "Wave 5: dispatched (BASE a)")
    r = ledger_cmd(tmp_path, "sweep-guard", str(p))
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"'{plan}' does not parse" in r.stderr


def test_a_malformed_second_sweep_plan_still_counts_as_a_second(tmp_path):
    p = ledger(tmp_path, "Sweep plan: waves 2-2", "Sweep plan: waves 3–3")
    r = ledger_cmd(tmp_path, "sweep-guard", str(p))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "one sweep pass" in r.stderr


# --- lint ---------------------------------------------------------------------

EVERY_FORMAT = (
    "Sweep: introduced — retry loses the key — from Task 4 review",
    "Sweep: pre-existing — substring match — from final review #1 — evidence: wdd:199 at cfbe04a",
    "Sorting: review #1 at 9f2c1ab",
    "Route: sweep #10 full — substring match",
    "Owner: rename the flag — keep it (2026-10-14)",
    "Amendment 1: written (v3)",
    "Sweep plan: waves 2-2",
    "Final review #1: CLEAN at 9f2c1ab",
    "Fixed: anchor the stop markers — closes #10",
    "Seen: #42 — wdd:285",
    "Filed: #51 — lane run exits 0",
    "Note: quoting edge — low value",
)


def test_lint_counts_every_documented_format(tmp_path):
    r = ledger_cmd(tmp_path, "lint", str(ledger(tmp_path, *EVERY_FORMAT)))
    assert r.returncode == 0, r.stderr
    for label in ("Sweep", "Sorting", "Route", "Owner", "Amendment", "Sweep plan",
                  "Final review", "Fixed", "Seen", "Filed", "Note"):
        assert f"{label}: " in r.stdout, label


def test_lint_rejects_a_pre_existing_sweep_line_without_evidence(tmp_path):
    p = ledger(tmp_path, "Sweep: pre-existing — substring match — from final review #1")
    r = ledger_cmd(tmp_path, "lint", str(p))
    assert r.returncode == 1
    assert "malformed: Sweep: pre-existing" in r.stderr


def test_lint_accepts_an_observation_from_the_re_review_after_a_full_branch_review(tmp_path):
    """The scoped re-review of review #k's fix is a source of its own."""
    p = ledger(tmp_path, "Sweep: introduced (late) — x — from final review #2 re-review")
    r = ledger_cmd(tmp_path, "lint", str(p))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Sweep: 1"


def test_lint_rejects_a_misspelled_route(tmp_path):
    r = ledger_cmd(tmp_path, "lint", str(ledger(tmp_path, "Route: fix-later — x")))
    assert r.returncode == 1


def test_lint_ignores_lines_with_no_known_prefix(tmp_path):
    r = ledger_cmd(tmp_path, "lint", str(ledger(tmp_path, "Ruling: a — b — c")))
    assert r.returncode == 0
    assert r.stdout == ""


# --- closes and sweep-section ---------------------------------------------------

SWEEP_LINES = (
    "Fixed: anchor the stop markers — closes #10",
    "Fixed: one residual — part of #25",
    "Fixed: an introduced nit",
    "Seen: #42 — wdd:285",
    "Filed: #51 — lane run exits 0",
    "Note: quoting edge — low value",
)


def test_closes_lists_only_full_fixes(tmp_path):
    r = ledger_cmd(tmp_path, "closes", str(ledger(tmp_path, *SWEEP_LINES)))
    assert r.returncode == 0
    assert r.stdout.split() == ["10"]


def test_a_mid_line_fixed_is_not_a_fixed_line(tmp_path):
    p = ledger(tmp_path, "Ruling: see Fixed: x — closes #10 — y — z")
    assert ledger_cmd(tmp_path, "closes", str(p)).stdout == ""


def test_the_sweep_section_counts_and_lists(tmp_path):
    r = ledger_cmd(tmp_path, "sweep-section", str(ledger(tmp_path, *SWEEP_LINES)))
    assert r.returncode == 0
    out = r.stdout
    assert out.startswith("## Sweep\n")
    assert ("**Sweep:** 3 fixed (closes #10) · 1 filed (#51) · "
            "1 seen again (#42) · 1 noted") in out
    assert out.count("Closes #10") == 1
    assert "Refs #25 — fixed: `one residual`" in out
    assert "Refs #42 — seen again at `wdd:285`" in out
    assert "Filed: #51 — `lane run exits 0`" in out
    assert "- `quoting edge` — `low value`" in out


def test_free_text_in_the_sweep_section_cannot_close_or_mention_anything(tmp_path):
    """A ledger one-liner is prose. Bare in a PR body, `fixes #12` closes #12 on
    merge and `@someone` pings them; inside a code span GitHub does neither. A
    backtick in the text would end the span early, so it is replaced first."""
    p = ledger(tmp_path, "Note: the loader `fixes #12 only on retry — cc @someone")
    r = ledger_cmd(tmp_path, "sweep-section", str(p))
    assert r.returncode == 0, r.stderr
    assert "- `the loader 'fixes #12 only on retry` — `cc @someone`" in r.stdout
    outside_spans = re.sub(r"`[^`]*`", "", r.stdout)
    assert "#12" not in outside_spans
    assert "@someone" not in outside_spans


def test_an_unresolved_close_is_dropped_but_still_counted_as_fixed(tmp_path):
    r = ledger_cmd(tmp_path, "sweep-section", str(ledger(tmp_path, *SWEEP_LINES)),
                   "--unresolved", "10")
    assert "Closes #10" not in r.stdout
    assert "**Sweep:** 3 fixed · 1 filed" in r.stdout


def test_no_sweep_lines_means_no_section(tmp_path):
    p = ledger(tmp_path, "Wave 1: integrated (merge a, pytest 1 passed)")
    r = ledger_cmd(tmp_path, "sweep-section", str(p))
    assert r.returncode == 0
    assert r.stdout == ""


# --- verdict and chain ----------------------------------------------------------


def commit(repo, msg):
    repo.git("commit", "-q", "--allow-empty", "-m", msg)
    return repo.git("rev-parse", "HEAD").stdout.strip()


def verdict(ws: Path, k: int, reviewed: str, since: str, word: str = "CLEAN") -> Path:
    ws.mkdir(exist_ok=True)
    p = ws / f"final-review-{k}.md"
    p.write_text(f"Reviewed: {reviewed}\nSince: {since}\nVerdict: {word}\n\n## Strengths\n")
    return p


def chain(repo, ws, head):
    return run_script("wdd-ledger", "chain", str(ws), head, "origin/main",
                      cwd=repo.path, env=repo.env)


def branch(repo):
    """origin/main at the seed commit; two branch commits on top of it."""
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    repo.git("update-ref", "refs/remotes/origin/main", base)
    return base, commit(repo, "one"), commit(repo, "two")


def test_one_clean_review_of_head_passes(repo):
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base)
    r = chain(repo, ws, one)
    assert r.returncode == 0, r.stderr


def test_no_verdict_refuses_rule_1(repo):
    _, one, _ = branch(repo)
    (repo.path / "ws").mkdir()
    r = chain(repo, repo.path / "ws", one)
    assert r.returncode == 1
    assert "rule 1" in r.stderr


def test_a_malformed_header_refuses_rule_1(repo):
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    ws.mkdir()
    (ws / "final-review-1.md").write_text(f"Reviewed: {one}\nVerdict: CLEAN\n")
    r = chain(repo, ws, one)
    assert r.returncode == 1
    assert "rule 1" in r.stderr


def test_a_gap_in_the_chain_refuses_rule_1(repo):
    base, one, two = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base)
    verdict(ws, 3, two, one)
    r = chain(repo, ws, two)
    assert r.returncode == 1
    assert "final-review-2.md" in r.stderr


def test_a_must_fix_newest_verdict_refuses_rule_2(repo):
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base, "MUST-FIX")
    r = chain(repo, ws, one)
    assert r.returncode == 1
    assert "rule 2" in r.stderr


def test_a_commit_after_the_review_refuses_rule_3(repo):
    base, one, two = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base)
    r = chain(repo, ws, two)
    assert r.returncode == 1
    assert "rule 3" in r.stderr


def test_a_chained_second_review_passes(repo):
    base, one, two = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base, "MUST-FIX")
    verdict(ws, 2, two, one)
    assert chain(repo, ws, two).returncode == 0


def test_a_second_review_that_skips_commits_refuses_rule_4(repo):
    base, one, two = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base, "MUST-FIX")
    verdict(ws, 2, two, base)
    r = chain(repo, ws, two)
    assert r.returncode == 1
    assert "rule 4" in r.stderr


def test_a_first_review_starting_on_the_branch_refuses_rule_5(repo):
    _, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, one)
    r = chain(repo, ws, one)
    assert r.returncode == 1
    assert "rule 5" in r.stderr


def test_a_first_review_whose_since_is_not_an_ancestor_of_reviewed_refuses_rule_5(repo):
    """Since_1 is on the default branch but off the reviewed line: rule 5, not rule 4."""
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    side = commit(repo, "side")
    repo.git("reset", "-q", "--hard", base)
    one = commit(repo, "one")
    repo.git("update-ref", "refs/remotes/origin/main", side)
    ws = repo.path / "ws"
    verdict(ws, 1, one, side)
    r = chain(repo, ws, one)
    assert r.returncode == 1
    assert "rule 5" in r.stderr


def test_a_second_review_whose_since_is_not_an_ancestor_of_its_reviewed_refuses_rule_4(repo):
    """Since_2 == Reviewed_1 holds, but Reviewed_1 is not an ancestor of Reviewed_2."""
    base = repo.git("rev-parse", "HEAD").stdout.strip()
    one = commit(repo, "one")
    repo.git("reset", "-q", "--hard", base)
    two = commit(repo, "two")
    repo.git("update-ref", "refs/remotes/origin/main", base)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base, "MUST-FIX")
    verdict(ws, 2, two, one)
    r = chain(repo, ws, two)
    assert r.returncode == 1
    assert "rule 4" in r.stderr


def test_an_unresolvable_default_ref_refuses_rule_5_by_name(repo):
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base)
    r = run_script("wdd-ledger", "chain", str(ws), one, "origin/nonexistent",
                   cwd=repo.path, env=repo.env)
    assert r.returncode == 1
    assert "rule 5" in r.stderr


def test_the_chain_still_passes_once_the_branch_has_merged(repo):
    """Rule 5 is ancestry, not equality with today's merge-base."""
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 1, one, base)
    repo.git("update-ref", "refs/remotes/origin/main", one)
    assert chain(repo, ws, one).returncode == 0


def test_verdict_prints_the_parsed_header(repo):
    base, one, _ = branch(repo)
    p = verdict(repo.path / "ws", 1, one, base)
    r = run_script("wdd-ledger", "verdict", str(p), cwd=repo.path, env=repo.env)
    assert r.returncode == 0
    assert r.stdout.splitlines() == [f"Reviewed={one}", f"Since={base}", "Verdict=CLEAN"]


def test_a_lone_review_zero_is_no_review_at_all(repo):
    """Reviews count from 1. A `final-review-0.md` is not one, so the chain is empty."""
    base, one, _ = branch(repo)
    ws = repo.path / "ws"
    verdict(ws, 0, one, base)
    r = chain(repo, ws, one)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "rule 1" in r.stderr


def test_an_unknown_command_is_a_usage_error(tmp_path):
    assert ledger_cmd(tmp_path, "bogus", "x").returncode == 2


def test_extra_arguments_are_a_usage_error(tmp_path):
    r = ledger_cmd(tmp_path, "stops", str(ledger(tmp_path, "Task 2: BLOCKED")), "extra")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bad usage" in r.stderr


def test_a_non_numeric_unresolved_list_is_a_usage_error(tmp_path):
    r = ledger_cmd(tmp_path, "sweep-section", str(ledger(tmp_path, *SWEEP_LINES)),
                   "--unresolved", "10,x")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "bad usage" in r.stderr


def test_an_undecodable_ledger_is_unreadable_input_not_bad_usage(tmp_path):
    """Usage is checked by counting arguments, never inferred from an exception --
    a decode error is a ValueError, and catching that as 'bad usage' named the
    wrong fault."""
    p = tmp_path / "progress.md"
    p.write_bytes(HEADER.encode() + b"Task 2: \xff\xfe BLOCKED\n")
    r = ledger_cmd(tmp_path, "stops", str(p))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unreadable" in r.stderr
    assert "bad usage" not in r.stderr
