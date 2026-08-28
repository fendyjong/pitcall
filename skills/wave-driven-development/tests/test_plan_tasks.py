"""plan-tasks must survive the shapes real plan files take, not tidy ones.

The synthetic plans below are deliberately awkward — a heading inside a fence,
a fence inside a fence, a line-range suffix on a declared path, prose the
controller reads edges out of. Each shape is one a real plan produced and this
parser got wrong: a naive fence toggle once truncated a 507-line brief to 129,
handing an implementer half its requirements with no error anywhere.
"""

from pathlib import Path

from conftest import run_script

SYNTHETIC = """\
# Some Plan

### Task 1: First thing

**Files:**
- Create: `app/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: nothing — first task.
- Produces: `build_wiring() -> Wiring`

- [ ] **Step 1: do it**

```python
### Task 99: not a real task, it is inside a fence
```

### Task 2: Second thing

**Files:**
- Modify: `app/main.py:10-40`
- Create: `app/other.py`

**Interfaces:**
- Consumes: `build_wiring` from Task 1.
- Produces: nothing.

### Task 3: Third thing

**Files:**
- Create: `app/third.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2 beyond a green tree.
- Produces: `third()`
"""


def _plan(tmp_path: Path) -> Path:
    p = tmp_path / "plan.md"
    p.write_text(SYNTHETIC)
    return p


def test_lists_every_task_with_title(tmp_path):
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "## Task 1: First thing" in r.stdout
    assert "## Task 2: Second thing" in r.stdout
    assert "## Task 3: Third thing" in r.stdout


def test_ignores_task_headings_inside_code_fences(tmp_path):
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert "Task 99" not in r.stdout


def test_strips_line_range_suffix_from_paths(tmp_path):
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert "  app/main.py\n" in r.stdout
    assert "app/main.py:10-40" not in r.stdout


def test_reports_overlapping_paths_as_a_wave_conflict(tmp_path):
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert "Task 1 & Task 2: app/main.py" in r.stdout


def test_two_tasks_declaring_independence_still_have_their_overlap_reported(tmp_path):
    """The collision `Interfaces` cannot express — and the reason this script exists.

    Both tasks below say they consume nothing from the other. A reader of the
    Interfaces blocks, however careful, concludes they can share a wave; the
    declared paths say otherwise, and only this tool compares them. The pair
    that DOES declare a dependency (Tasks 1 & 2 in SYNTHETIC) is the easy
    direction and is covered above.

    Asserting only that non-overlapping tasks are silent — the test below —
    is a one-directional guard on a two-directional property: reporting
    nothing at all passes it.
    """
    p = tmp_path / "independent.md"
    p.write_text(
        "### Task 1: A\n\n**Files:**\n- Create: `app/a.py`\n\n"
        "**Interfaces:**\n- Consumes: nothing.\n- Produces: `a()`\n\n"
        "### Task 2: B\n\n**Files:**\n- Create: `app/b.py`\n- Modify: `docs/shared.md`\n\n"
        "**Interfaces:**\n- Consumes: nothing from Task 1.\n- Produces: `b()`\n\n"
        "### Task 3: C\n\n**Files:**\n- Create: `app/c.py`\n- Modify: `docs/shared.md`\n\n"
        "**Interfaces:**\n- Consumes: nothing from Tasks 1-2.\n- Produces: `c()`\n"
    )
    r = run_script("plan-tasks", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Task 2 & Task 3: docs/shared.md" in r.stdout
    assert "(none)" not in r.stdout


def test_non_overlapping_tasks_are_not_reported(tmp_path):
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert "Task 1 & Task 3" not in r.stdout
    assert "Task 2 & Task 3" not in r.stdout


def test_no_overlaps_at_all_prints_none(tmp_path):
    p = tmp_path / "clean.md"
    p.write_text(
        "### Task 1: A\n\n**Files:**\n- Create: `a.py`\n\n"
        "**Interfaces:**\n- Consumes: nothing.\n- Produces: `a()`\n\n"
        "### Task 2: B\n\n**Files:**\n- Create: `b.py`\n\n"
        "**Interfaces:**\n- Consumes: nothing.\n- Produces: `b()`\n"
    )
    r = run_script("plan-tasks", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "# File overlaps" in r.stdout
    assert "(none)" in r.stdout


def test_interfaces_prose_survives_verbatim(tmp_path):
    """The controller infers edges from this text — losing it loses the DAG."""
    r = run_script("plan-tasks", str(_plan(tmp_path)), cwd=tmp_path)
    assert "beyond a green tree" in r.stdout


def test_a_sub_bulleted_produces_block_survives_every_line(tmp_path):
    """Real plans write `Produces:` as a heading over indented sub-bullets.

    The controller infers edges from this prose, so a dropped sub-bullet is a
    dropped interface. Nothing in the parser is written specifically to keep
    them: the Interfaces branch appends every non-empty line, and the
    sub-bullets survive because of that and nothing else. A refactor to "take
    only the `- Produces:` line" would pass every other test in this file.
    """
    p = tmp_path / "subbullets.md"
    p.write_text(
        "### Task 1: A\n\n**Files:**\n- Create: `app/a.py`\n\n"
        "**Interfaces:**\n- Consumes: nothing.\n- Produces:\n"
        "  - `models.LineItem` — one row of an order\n"
        "  - `models.Invoice` — its parent, created in the same transaction\n"
    )
    r = run_script("plan-tasks", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "models.LineItem" in r.stdout
    assert "models.Invoice" in r.stdout
    assert "created in the same transaction" in r.stdout


def test_missing_plan_file_exits_2(tmp_path):
    r = run_script("plan-tasks", str(tmp_path / "nope.md"), cwd=tmp_path)
    assert r.returncode == 2
    assert "cannot read plan file" in r.stderr


def test_usage_error_exits_2(tmp_path):
    r = run_script("plan-tasks", cwd=tmp_path)
    assert r.returncode == 2
    assert "usage:" in r.stderr


def test_plan_without_tasks_exits_3(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("# Just a heading\n\nNo tasks here.\n")
    r = run_script("plan-tasks", str(p), cwd=tmp_path)
    assert r.returncode == 3
    assert "no tasks found" in r.stderr


def test_four_backtick_fence_survives_a_nested_three_backtick_fence(tmp_path):
    """A fenced example that itself contains a fence must not leak task headings.

    A naive open/close toggle desynchronises here and invents Task 42, putting
    a phantom node in the wave graph.

    The ghost heading must sit INSIDE the inner ```python fence, not directly
    inside the outer ````markdown one. At the outer level both trackers agree
    that the line is fenced, so a heading there proves nothing; one line deeper
    the naive toggle has already flipped itself back to "outside" and reads the
    heading as real. Do not "tidy" this ordering — it is the whole test.
    """
    p = tmp_path / "nested.md"
    p.write_text(
        "### Task 1: Real\n\n**Files:**\n- Create: `a.py`\n\n"
        "**Interfaces:**\n- Consumes: nothing.\n- Produces: `a()`\n\n"
        "````markdown\n"
        "```python\n"
        "### Task 42: ghost inside a nested fence\n"
        "```\n"
        "````\n"
    )
    r = run_script("plan-tasks", str(p), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "Task 42" not in r.stdout
    assert "## Task 1: Real" in r.stdout
