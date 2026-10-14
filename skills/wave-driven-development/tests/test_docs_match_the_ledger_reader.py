"""The formats the docs show are the formats `wdd-ledger` reads.

SKILL.md's example ledger (its one ```ledger block) is fed through
`wdd-ledger lint`, and must show every sweep-era line format; the verdict
header example in final-reviewer-prompt.md (its one ```verdict block) is fed
through `wdd-ledger verdict`. A format written in two places drifts — #35's
lesson about a path written twice — so the documented copy is executed
rather than trusted.
"""

import re
from pathlib import Path

from conftest import run_script

SKILL_DIR = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^```(\w+)\n(.*?)^```$", re.M | re.S)

EVERY_SWEEP_FORMAT = ("Sweep", "Sorting", "Route", "Owner", "Amendment", "Sweep plan",
                      "Final review", "Fixed", "Seen", "Filed", "Note")


def only_block(path: Path, info: str) -> str:
    blocks = [body for name, body in FENCE.findall(path.read_text(encoding="utf-8"))
              if name == info]
    assert len(blocks) == 1, f"{path.name} must carry exactly one ```{info} block, found {len(blocks)}"
    return blocks[0]


def test_the_example_ledger_parses_and_shows_every_sweep_format(tmp_path):
    p = tmp_path / "progress.md"
    p.write_text(only_block(SKILL_DIR / "SKILL.md", "ledger"), encoding="utf-8")
    r = run_script("wdd-ledger", "lint", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    for label in EVERY_SWEEP_FORMAT:
        assert f"{label}: " in r.stdout, f"SKILL.md's example ledger shows no {label!r} line"


def test_the_example_ledger_passes_the_stop_and_sweep_checks(tmp_path):
    """It shows a stop marker and its resolution, and one sweep in range."""
    p = tmp_path / "progress.md"
    p.write_text(only_block(SKILL_DIR / "SKILL.md", "ledger"), encoding="utf-8")
    assert run_script("wdd-ledger", "stops", str(p), cwd=tmp_path).returncode == 0
    assert run_script("wdd-ledger", "sweep-guard", str(p), cwd=tmp_path).returncode == 0
    assert "BLOCKED" in p.read_text(), "the example should show a stop marker being resolved"


def test_the_final_reviewers_verdict_example_parses(tmp_path):
    p = tmp_path / "final-review-1.md"
    p.write_text(only_block(SKILL_DIR / "final-reviewer-prompt.md", "verdict"), encoding="utf-8")
    r = run_script("wdd-ledger", "verdict", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-1] == "Verdict=CLEAN"
