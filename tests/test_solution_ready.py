"""The solution-ready gate: every case proved offline.

`decide` is pure by construction so this file never needs a network or a
repository. That is the whole reason for the seam -- see the module docstring
in `scripts/solution_ready.py`.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import solution_ready  # noqa: E402

LABEL = solution_ready.LABEL

#: The author gate's trusted side. Every pre-existing case is a maintainer's
#: own issue, which is what the gate is built to let through.
INSIDER = "OWNER"
OUTSIDER = "NONE"

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

#: What copying the README produces: the documented example quoted in "What",
#: above the issue's own section. Each `Expected today:` line belongs to a
#: different check, and only the section's own one may be read.
BODY_QUOTED_EXAMPLE = """## What

The gate reads a section that looks like this:

```
python3 -m pytest tests/test_example.py -q
```

Expected today: FAILS with `AssertionError: 4 != 5`

Ours is below.

## Failing check

```
python3 -m pytest tests/test_widget.py -q
```

Expected today: FAILS
"""


#: The README's example is a fenced block that CONTAINS the heading, so pasting
#: it puts a quoted `## Failing check` above the issue's own one.
BODY_PASTED_README = """## What

The format the gate wants looks like this:

````markdown
## Failing check

```
python3 -m pytest tests/test_example.py -q
```

Expected today: FAILS with `AssertionError: 4 != 5`
````

## Failing check

```
python3 -m pytest tests/test_widget.py -q
```

Expected today: FAILS with `AssertionError: 7 != 8`
"""


def test_extract_command_takes_the_first_fenced_block_under_the_heading():
    assert solution_ready.extract_command(BODY) == "python3 -m pytest tests/test_widget.py -q"


def test_extract_command_is_none_without_the_heading():
    assert solution_ready.extract_command(BODY_NO_SECTION) is None


def test_extract_command_ignores_a_fence_that_precedes_the_heading():
    body = "```\nnot the check\n```\n\n" + BODY
    assert solution_ready.extract_command(body) == "python3 -m pytest tests/test_widget.py -q"


def test_extract_command_is_none_when_the_fence_is_never_closed():
    """An unterminated fence would otherwise run the rest of the issue."""
    body = "## Failing check\n\n```\npython3 -m pytest -q\n\nExpected today: FAILS `boom`\n"
    assert solution_ready.extract_command(body) is None


def test_expected_fragments_reads_the_backticked_text():
    assert solution_ready.expected_fragments(BODY) == ["AssertionError: 4 != 5"]


def test_expected_fragments_is_empty_when_nothing_is_backticked():
    assert solution_ready.expected_fragments(BODY_NO_FRAGMENT) == []


def test_a_heading_inside_a_fence_is_quoted_text_not_a_section():
    """The documented example is a fenced block carrying this very heading."""
    assert solution_ready.extract_command(BODY_PASTED_README) == (
        "python3 -m pytest tests/test_widget.py -q")
    assert solution_ready.expected_fragments(BODY_PASTED_README) == [
        "AssertionError: 7 != 8"]


def test_expected_fragments_reads_the_sections_own_line_not_an_earlier_one():
    """The close guard is fail-OPEN if it can read a quoted example's fragment."""
    assert solution_ready.expected_fragments(BODY_QUOTED_EXAMPLE) == []


def test_a_quoted_example_above_the_section_cannot_close_the_issue():
    action, reason = solution_ready.decide(
        BODY_QUOTED_EXAMPLE, [LABEL], INSIDER, (0, "1 passed"))
    assert action == "refuse"
    assert "never seen to fail" in reason


def test_unlabelled_issue_is_refused():
    action, reason = solution_ready.decide(BODY, [], INSIDER, None)
    assert action == "refuse"
    assert LABEL in reason


def test_an_outsiders_issue_is_refused_even_when_labelled():
    """The label authorizes a snapshot; the body is fetched live and its author
    can rewrite it afterwards, so the author is gated too."""
    action, reason = solution_ready.decide(BODY, [LABEL], OUTSIDER, None)
    assert action == "refuse"
    assert OUTSIDER in reason


def test_an_outsiders_issue_is_refused_at_the_verdict_too():
    action, _ = solution_ready.decide(BODY, [LABEL], OUTSIDER, (0, "1 passed"))
    assert action == "refuse"


def test_a_missing_association_is_refused():
    """A field the API did not return is never a pass."""
    action, _ = solution_ready.decide(BODY, [LABEL], None, None)
    assert action == "refuse"


@pytest.mark.parametrize("association", solution_ready.TRUSTED_ASSOCIATIONS)
def test_each_trusted_association_is_admitted(association):
    action, _ = solution_ready.decide(BODY, [LABEL], association, None)
    assert action == "run"


def test_missing_failing_check_section_is_refused():
    action, reason = solution_ready.decide(BODY_NO_SECTION, [LABEL], INSIDER, None)
    assert action == "refuse"
    assert solution_ready.HEADING in reason


def test_a_labelled_issue_with_a_command_asks_to_run_it():
    action, detail = solution_ready.decide(BODY, [LABEL], INSIDER, None)
    assert action == "run"
    assert detail == "python3 -m pytest tests/test_widget.py -q"


def test_check_fails_as_recorded_proceeds():
    result = (1, "E   AssertionError: 4 != 5\n1 failed")
    action, _ = solution_ready.decide(BODY, [LABEL], INSIDER, result)
    assert action == "proceed"


def test_check_fails_differently_is_refused():
    result = (1, "E   ImportError: no module named widget")
    action, reason = solution_ready.decide(BODY, [LABEL], INSIDER, result)
    assert action == "refuse"
    assert "not the failure" in reason


def test_check_passes_with_a_recorded_failure_closes():
    action, _ = solution_ready.decide(BODY, [LABEL], INSIDER, (0, "1 passed"))
    assert action == "close"


def test_check_passes_with_no_recorded_failure_is_refused():
    """The guard: without an observed failure the label was never earned."""
    action, reason = solution_ready.decide(
        BODY_NO_FRAGMENT, [LABEL], INSIDER, (0, "1 passed"))
    assert action == "refuse"
    assert "never seen to fail" in reason


def test_check_fails_with_no_recorded_failure_is_refused():
    action, _ = solution_ready.decide(BODY_NO_FRAGMENT, [LABEL], INSIDER, (1, "boom"))
    assert action == "refuse"


@pytest.mark.parametrize("piece", ["the-command", "the-output", "2026-01-02"])
def test_close_comment_carries_command_output_and_date(piece):
    body = solution_ready.close_comment("the-command", "the-output", "2026-01-02")
    assert piece in body


def test_close_comment_fences_output_that_contains_a_fence():
    """A three-backtick fence around output containing one closes early."""
    body = solution_ready.close_comment("cmd", "before ``` after", "2026-01-02")
    assert body.count("\n````\n") == 2


def test_close_comment_truncates_long_output_and_says_so():
    output = "x" * (solution_ready.MAX_OUTPUT_CHARS + 500) + "TAIL"
    body = solution_ready.close_comment("cmd", output, "2026-01-02")
    assert len(body) < solution_ready.MAX_OUTPUT_CHARS + 2000
    assert "TAIL" in body
    assert "truncated" in body


def test_run_check_runs_without_a_shell():
    """RED the moment anyone adds `shell=True`: a shell would run two commands
    and print `a` and `b` on separate lines. Without one, `a;`, `echo` and `b`
    are literal arguments to a single `echo`."""
    returncode, output = solution_ready.run_check("echo a; echo b")
    assert returncode == 0
    assert output.strip() == "a; echo b"


@pytest.mark.parametrize("command", [
    "pytest -q 2>&1 | grep -q FAILED",
    "pytest -q > out.txt",
    "pytest -q && echo done",
])
def test_run_check_refuses_a_shell_operator(command):
    """Passing an operator through as an argument derives the verdict from
    garbage, silently. Refusing loudly is what protects the filer."""
    with pytest.raises(solution_ready.tracker.TrackerError) as exc:
        solution_ready.run_check(command)
    assert "WITHOUT a shell" in str(exc.value)


def test_run_check_keeps_an_operator_inside_a_token():
    """`a|b` is a legitimate regex argument, not a pipe."""
    returncode, output = solution_ready.run_check("echo 'a|b'")
    assert returncode == 0
    assert output.strip() == "a|b"


def _fake_tracker_run(calls, body=BODY, association=INSIDER):
    def run(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("gh", "repo", "view"):
            return "acme/widgets\n"
        if args[:2] == ("gh", "api"):
            return json.dumps({"body": body, "labels": [LABEL],
                               "association": association})
        return ""
    return run


def test_close_comments_before_closing_and_exits_three(monkeypatch, capsys):
    """The DoD, verified by observation: a passing check leaves a comment
    carrying the command and the date, and only THEN closes. The ordering is
    load-bearing -- a failed comment must leave the issue open rather than
    closed with no evidence."""
    calls = []
    monkeypatch.setattr(solution_ready.tracker, "run", _fake_tracker_run(calls))
    monkeypatch.setattr(solution_ready, "run_check", lambda command: (0, "1 passed"))

    assert solution_ready.main(["7"]) == 3

    verbs = [args for args in calls if args[:2] == ("gh", "issue")]
    assert [args[2] for args in verbs] == ["comment", "close"]

    comment = verbs[0]
    body = comment[comment.index("--body") + 1]
    assert "python3 -m pytest tests/test_widget.py -q" in body
    assert date.today().isoformat() in body
    assert "1 passed" in body
    assert "solution-ready: closed" in capsys.readouterr().out


def test_proceed_exits_zero_and_writes_nothing(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(solution_ready.tracker, "run", _fake_tracker_run(calls))
    monkeypatch.setattr(solution_ready, "run_check",
                        lambda command: (1, "E   AssertionError: 4 != 5"))

    assert solution_ready.main(["7"]) == 0

    assert [args for args in calls if args[:2] == ("gh", "issue")] == []
    assert "solution-ready: proceed" in capsys.readouterr().out


def test_dry_run_reports_the_close_verdict_without_writing(monkeypatch, capsys):
    """The status reports the VERDICT, not whether anything was written."""
    calls = []
    monkeypatch.setattr(solution_ready.tracker, "run", _fake_tracker_run(calls))
    monkeypatch.setattr(solution_ready, "run_check", lambda command: (0, "1 passed"))

    assert solution_ready.main(["7", "--dry-run"]) == 3

    assert [args for args in calls if args[:2] == ("gh", "issue")] == []
    assert "solution-ready: closed" in capsys.readouterr().out


def test_an_outsiders_labelled_issue_never_runs_the_check(monkeypatch):
    """The whole point of the author gate: nothing is executed at all."""
    calls = []
    monkeypatch.setattr(solution_ready.tracker, "run",
                        _fake_tracker_run(calls, association=OUTSIDER))

    def never(command, cwd=None):
        raise AssertionError("the check must not run for an outsider's issue")

    monkeypatch.setattr(solution_ready, "run_check", never)

    with pytest.raises(solution_ready.tracker.TrackerError) as exc:
        solution_ready.main(["7"])
    assert OUTSIDER in str(exc.value)
