"""The solution-ready gate: every case proved offline.

`decide` is pure by construction so this file never needs a network or a
repository. That is the whole reason for the seam -- see the module docstring
in `scripts/solution_ready.py`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import solution_ready  # noqa: E402

LABEL = solution_ready.LABEL

BODY = """## What

The widget counter is off by one.

## Failing check

```
python3 -m pytest tests/test_widget.py -q
```

Expected today: FAILS with `AssertionError: 4 != 5`
"""

BODY_NO_FRAGMENT = BODY.replace("FAILS with `AssertionError: 4 != 5`", "FAILS")
BODY_NO_SECTION = BODY.split("## Failing check")[0]


def test_extract_command_takes_the_first_fenced_block_under_the_heading():
    assert solution_ready.extract_command(BODY) == "python3 -m pytest tests/test_widget.py -q"


def test_extract_command_is_none_without_the_heading():
    assert solution_ready.extract_command(BODY_NO_SECTION) is None


def test_extract_command_ignores_a_fence_that_precedes_the_heading():
    body = "```\nnot the check\n```\n\n" + BODY
    assert solution_ready.extract_command(body) == "python3 -m pytest tests/test_widget.py -q"


def test_expected_fragments_reads_the_backticked_text():
    assert solution_ready.expected_fragments(BODY) == ["AssertionError: 4 != 5"]


def test_expected_fragments_is_empty_when_nothing_is_backticked():
    assert solution_ready.expected_fragments(BODY_NO_FRAGMENT) == []


def test_unlabelled_issue_is_refused():
    action, reason = solution_ready.decide(BODY, [], None)
    assert action == "refuse"
    assert LABEL in reason


def test_missing_failing_check_section_is_refused():
    action, reason = solution_ready.decide(BODY_NO_SECTION, [LABEL], None)
    assert action == "refuse"
    assert solution_ready.HEADING in reason


def test_a_labelled_issue_with_a_command_asks_to_run_it():
    action, detail = solution_ready.decide(BODY, [LABEL], None)
    assert action == "run"
    assert detail == "python3 -m pytest tests/test_widget.py -q"


def test_check_fails_as_recorded_proceeds():
    result = (1, "E   AssertionError: 4 != 5\n1 failed")
    action, _ = solution_ready.decide(BODY, [LABEL], result)
    assert action == "proceed"


def test_check_fails_differently_is_refused():
    result = (1, "E   ImportError: no module named widget")
    action, reason = solution_ready.decide(BODY, [LABEL], result)
    assert action == "refuse"
    assert "not the failure" in reason


def test_check_passes_with_a_recorded_failure_closes():
    action, _ = solution_ready.decide(BODY, [LABEL], (0, "1 passed"))
    assert action == "close"


def test_check_passes_with_no_recorded_failure_is_refused():
    """The guard: without an observed failure the label was never earned."""
    action, reason = solution_ready.decide(BODY_NO_FRAGMENT, [LABEL], (0, "1 passed"))
    assert action == "refuse"
    assert "never seen to fail" in reason


def test_check_fails_with_no_recorded_failure_is_refused():
    action, _ = solution_ready.decide(BODY_NO_FRAGMENT, [LABEL], (1, "boom"))
    assert action == "refuse"


@pytest.mark.parametrize("piece", ["the-command", "the-output", "2026-01-02"])
def test_close_comment_carries_command_output_and_date(piece):
    body = solution_ready.close_comment("the-command", "the-output", "2026-01-02")
    assert piece in body
