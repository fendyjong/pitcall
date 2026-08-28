"""Mechanical validation of a wave plan.

Every rule here reads a declared field. None interprets prose — that is the
property that makes validation a gate rather than a second opinion.
"""

from pathlib import Path

from conftest import run_script


def _plan(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "plan.md"
    p.write_text(body)
    return p


VALID = """\
# A Plan

## Waves

| Wave | Tasks | Rationale |
|---|---|---|
| 1 | 1, 2 | disjoint, no deps |
| 2 | 3 | depends on 1 |

### Task 1: First

**Files:**
- Create: `app/one.py`

**Interfaces:**
- Depends: none
- Model: cheapest
- Produces: `one()`

### Task 2: Second

**Files:**
- Create: `app/two.py`

**Interfaces:**
- Depends: none
- Model: cheapest
- Produces: `two()`

### Task 3: Third

**Files:**
- Modify: `app/one.py`

**Interfaces:**
- Depends: 1
- Model: cheapest
- Produces: nothing
"""


def test_a_compliant_plan_passes(tmp_path):
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, VALID)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "3 tasks" in r.stdout
    assert "2 waves" in r.stdout


def test_overlapping_files_in_one_wave_fail(tmp_path):
    """The whole point: Tasks 1 and 2 would collide at the wave merge."""
    body = VALID.replace("- Create: `app/two.py`", "- Modify: `app/one.py`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "wave 1" in r.stderr
    assert "app/one.py" in r.stderr
    assert "Task 1" in r.stderr and "Task 2" in r.stderr


def test_dependency_inside_the_same_wave_fails(tmp_path):
    body = VALID.replace("| 1 | 1, 2 |", "| 1 | 1, 2, 3 |").replace("| 2 | 3 | depends on 1 |\n", "")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 3" in r.stderr
    assert "same wave" in r.stderr or "earlier wave" in r.stderr


def test_dependency_on_a_later_wave_fails(tmp_path):
    body = VALID.replace("- Depends: 1\n- Produces: nothing", "- Depends: 2\n- Produces: nothing")
    body = body.replace("| 1 | 1, 2 |", "| 1 | 1, 3 |").replace("| 2 | 3 |", "| 2 | 2 |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "earlier wave" in r.stderr


def test_task_in_no_wave_fails(tmp_path):
    body = VALID.replace("| 1 | 1, 2 |", "| 1 | 1 |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 2 is in no wave" in r.stderr


def test_task_in_two_waves_fails(tmp_path):
    body = VALID.replace("| 2 | 3 | depends on 1 |", "| 2 | 1, 3 | depends on 1 |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 1" in r.stderr
    assert "wave 1 and wave 2" in r.stderr


def test_wave_naming_a_nonexistent_task_fails(tmp_path):
    body = VALID.replace("| 2 | 3 | depends on 1 |", "| 2 | 3, 9 | depends on 1 |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 9" in r.stderr


def test_depends_on_a_nonexistent_task_fails(tmp_path):
    body = VALID.replace("- Depends: 1\n", "- Depends: 9\n")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 9" in r.stderr


def test_missing_depends_line_fails(tmp_path):
    body = VALID.replace(
        "- Depends: none\n- Model: cheapest\n- Produces: `two()`",
        "- Model: cheapest\n- Produces: `two()`",
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 2" in r.stderr
    assert "Depends" in r.stderr


def test_dependency_cycle_fails(tmp_path):
    body = VALID.replace(
        "- Depends: none\n- Model: cheapest\n- Produces: `one()`",
        "- Depends: 3\n- Model: cheapest\n- Produces: `one()`",
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "cycle" in r.stderr


def test_missing_model_line_fails(tmp_path):
    """Tier is assigned at planning time, so its absence must stop the plan.

    A wave is dispatched in one message, and the Agent tool's `model` is
    optional — omitting it silently inherits the session's own, usually most
    expensive, tier and leaves no trace anywhere. Undeclared is the failure
    this rule exists to make loud.
    """
    body = VALID.replace("- Depends: none\n- Model: cheapest\n- Produces: `two()`",
                         "- Depends: none\n- Produces: `two()`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 2" in r.stderr
    assert "Model" in r.stderr


def test_unknown_model_tier_fails_and_names_the_valid_tiers(tmp_path):
    """A typo'd tier must not read as a deliberate choice.

    The message lists the vocabulary because the planner who typo'd it is the
    one reading the failure.
    """
    body = VALID.replace("- Model: cheapest\n- Produces: `two()`",
                         "- Model: gpt-9-turbo\n- Produces: `two()`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "gpt-9-turbo" in r.stderr
    assert "cheapest, standard, most-capable" in r.stderr


def test_model_tier_is_case_insensitive(tmp_path):
    """`Model: Standard` is the same choice as `standard`.

    Plans are hand-written prose; rejecting a capitalised tier would be a
    formatting gate wearing a correctness gate's clothes.
    """
    body = VALID.replace("- Model: cheapest\n- Produces: `two()`",
                         "- Model: Most-Capable\n- Produces: `two()`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_task_declaring_no_files_fails(tmp_path):
    body = VALID.replace("**Files:**\n- Create: `app/two.py`\n\n", "**Files:**\n- (none)\n\n")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "Task 2" in r.stderr
    assert "no files" in r.stderr


def test_duplicate_migration_number_in_one_home_fails(tmp_path):
    """Different paths, same number, different waves — rule 4 cannot see this."""
    body = VALID.replace(
        "- Create: `app/one.py`", "- Create: `db/migrations/0042_a.sql`"
    ).replace("- Modify: `app/one.py`", "- Create: `db/migrations/0042_b.sql`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "0042" in r.stderr
    assert "db/migrations" in r.stderr


def test_same_migration_number_in_different_homes_passes(tmp_path):
    """Separate migration homes are separate databases. 0042 in each is correct."""
    body = VALID.replace(
        "- Create: `app/one.py`", "- Create: `db/migrations/0042_a.sql`"
    ).replace(
        "- Modify: `app/one.py`",
        "- Create: `services/billing/db/migrations/0042_b.sql`",
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_submodule_bump_collision_is_caught_by_path_disjointness(tmp_path):
    """`Bump:` is a path, so it needs no rule of its own."""
    body = VALID.replace("- Create: `app/one.py`", "- Bump: `libs/shared`").replace(
        "- Create: `app/two.py`", "- Bump: `libs/shared`"
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "libs/shared" in r.stderr


def test_delete_counts_as_ownership(tmp_path):
    """A task deleting a file another task modifies is a collision."""
    body = VALID.replace("- Create: `app/two.py`", "- Delete: `app/one.py`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "app/one.py" in r.stderr


def test_trailing_slash_alias_is_caught_as_a_collision(tmp_path):
    """`libs/shared` and `libs/shared/` name the same tree.

    Raw string comparison lets both spellings validate as disjoint and then
    collide for real at the wave merge.
    """
    body = VALID.replace("- Create: `app/one.py`", "- Bump: `libs/shared`").replace(
        "- Create: `app/two.py`", "- Bump: `libs/shared/`"
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "wave 1" in r.stderr
    assert "libs/shared" in r.stderr
    assert "Task 1" in r.stderr and "Task 2" in r.stderr


def test_leading_dot_slash_alias_is_caught_as_a_collision(tmp_path):
    """`./services/billing/app/checkout.py` and `services/billing/app/checkout.py` name the same file."""
    body = VALID.replace(
        "- Create: `app/one.py`", "- Modify: `services/billing/app/checkout.py`"
    ).replace("- Create: `app/two.py`", "- Modify: `./services/billing/app/checkout.py`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "wave 1" in r.stderr
    assert "services/billing/app/checkout.py" in r.stderr


def test_unbackticked_path_shaped_token_produces_a_warning(tmp_path):
    """A path mentioned without backticks is invisible to disjointness checking.

    The plan still validates -- a warning is not a failure -- but the gap must
    be surfaced rather than silently dropped: a validator that says "valid"
    while a real shared path sits unchecked in plain text makes the tool look
    broken. Task 2 keeps its own backticked file so Rule 6 does not also fire
    and mask the point of this test.
    """
    body = VALID.replace(
        "- Create: `app/two.py`",
        "- Create: `app/two.py`\n- Modify: app/one.py",
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "warning" in r.stderr
    assert "Task 2" in r.stderr
    assert "app/one.py" in r.stderr


def test_bare_filename_with_no_extension_produces_a_warning(tmp_path):
    """`Makefile` has neither `/` nor an extension, so looks_like_path() drops it.

    Restricted to the token immediately after the bullet's label: this must
    not fire on a symbol name quoted later in the line, which real plans do
    constantly (e.g. `` - Modify: `services/x.py` (`SomeClass`) ``).
    """
    body = VALID.replace("- Create: `app/two.py`", "- Create: `Makefile`")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1  # Task 2 now declares no checkable files (Rule 6)
    assert "warning" in r.stderr
    assert "Task 2" in r.stderr
    assert "Makefile" in r.stderr


def test_plan_without_a_waves_table_fails(tmp_path):
    body = VALID.split("### Task 1")[1]
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, "### Task 1" + body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "no ## Waves table" in r.stderr


def test_every_breach_is_reported_not_just_the_first(tmp_path):
    """A planner fixing one error at a time round-trips for every mistake."""
    body = VALID.replace("- Create: `app/two.py`", "- Modify: `app/one.py`").replace(
        "- Depends: 1\n", "- Depends: 9\n"
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "app/one.py" in r.stderr
    assert "Task 9" in r.stderr


def test_summary_mode_still_works(tmp_path):
    """--validate is an addition; the existing summary mode is unchanged."""
    r = run_script("plan-tasks", str(_plan(tmp_path, VALID)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "## Task 1: First" in r.stdout
    assert "# File overlaps" in r.stdout


def test_validate_on_a_missing_file_exits_2(tmp_path):
    r = run_script("plan-tasks", "--validate", str(tmp_path / "nope.md"), cwd=tmp_path)
    assert r.returncode == 2


def test_a_nested_fence_inside_the_waves_section_is_not_parsed(tmp_path):
    """The wave table must be read with the same fence tracking as tasks are.

    A naive toggle desynchronises on a fenced example containing a fence and
    then reads illustrative table rows as real wave membership — inventing a
    wave entry for a task that does not exist and rejecting a valid plan.
    """
    body = VALID.replace(
        "| 2 | 3 | depends on 1 |",
        "| 2 | 3 | depends on 1 |\n\nAn illustrative example:\n\n"
        "````markdown\n```\n| 2 | 9 |\n```\n````\n",
    )
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_a_wave_row_with_no_tasks_fails(tmp_path):
    body = VALID.replace("| 2 | 3 | depends on 1 |", "| 2 | 3 | depends on 1 |\n| 3 |  | vestigial |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "wave 3 declares no tasks" in r.stderr


def test_a_duplicate_wave_row_fails(tmp_path):
    """Two rows labelled the same wave silently merge, which hides a typo."""
    body = VALID.replace("| 2 | 3 | depends on 1 |", "| 1 | 3 | typo, meant wave 2 |")
    r = run_script("plan-tasks", "--validate", str(_plan(tmp_path, body)), cwd=tmp_path)
    assert r.returncode == 1
    assert "more than one row" in r.stderr
