"""The `runs-on` line scanner (Task 3)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
from garage_runs_on import scan, self_hosted_sets  # noqa: E402

WORKFLOW = """\
name: ci
on: [push]
jobs:
  scalar:
    runs-on: ubuntu-latest
  quoted:
    runs-on: "self-hosted"   # a comment
  inline:
    runs-on: [self-hosted, 'linux', "x64"]
  block:
    runs-on:
      - self-hosted
      - linux   # trailing comment
    steps:
      - run: echo hi
  compact:
    runs-on:
    - self-hosted
    - large
    steps: []
  expression:
    runs-on: ${{ matrix.os }}
  group:
    runs-on:
      group: big-runners
      labels: [self-hosted]
  folded:
    runs-on: >
      self-hosted
"""


def test_each_form_is_read_with_its_line_number():
    assert scan(WORKFLOW) == [
        (5, ("ubuntu-latest",)),
        (7, ("self-hosted",)),
        (9, ("self-hosted", "linux", "x64")),
        (11, ("self-hosted", "linux")),
        (17, ("self-hosted", "large")),
        (22, None),
        (24, None),
        (28, None),
    ]


def test_an_expression_inside_a_list_is_unreadable():
    assert scan("    runs-on: [self-hosted, '${{ inputs.size }}']\n") == [(1, None)]
    assert scan("    runs-on:\n      - self-hosted\n      - ${{ inputs.size }}\n") == [(1, None)]


def test_an_inline_list_split_over_lines_is_unreadable():
    assert scan("    runs-on: [self-hosted,\n      linux]\n") == [(1, None)]


def test_an_empty_value_or_list_is_unreadable():
    assert scan("    runs-on:\n    steps: []\n") == [(1, None)]
    assert scan("    runs-on: []\n") == [(1, None)]


def test_a_commented_out_runs_on_and_other_keys_are_ignored():
    assert scan("    # runs-on: [self-hosted]\n    run-on: x\n    name: runs-on\n") == []


def test_a_quoted_scalar_with_a_hash_keeps_it():
    assert scan("    runs-on: 'self-hosted#1'\n") == [(1, ("self-hosted#1",))]


def test_self_hosted_sets_dedupes_ignoring_case_and_order():
    files = {
        ".github/workflows/a.yml": "  runs-on: [self-hosted, Linux]\n  runs-on: ubuntu-latest\n",
        ".github/workflows/b.yaml": "  runs-on: [linux, Self-Hosted]\n  runs-on: ${{ x }}\n"
                                    "  runs-on: [self-hosted, large, LARGE]\n",
    }
    sets, unreadable = self_hosted_sets(files)
    assert sets == [["self-hosted", "Linux"], ["self-hosted", "large"]]
    assert unreadable == [".github/workflows/b.yaml:2"]


def test_no_self_hosted_job_means_no_sets():
    assert self_hosted_sets({"w.yml": "  runs-on: ubuntu-latest\n"}) == ([], [])


def test_a_mapping_where_a_label_belongs_is_unreadable():
    assert scan("    runs-on:\n      - group: big-runners\n") == [(1, None)]
    assert scan("    runs-on: [self-hosted, group: big-runners]\n") == [(1, None)]
    assert scan("    runs-on:\n      - labels:\n") == [(1, None)]
    assert scan("    runs-on: 'self-hosted: quoted'\n") == [(1, ("self-hosted: quoted",))]


def test_a_bare_dash_in_a_block_list_makes_it_unreadable():
    assert scan("runs-on:\n- self-hosted\n-\n- linux\n") == [(1, None)]
    assert scan("    runs-on:\n      - self-hosted\n      -   # nothing\n") == [(1, None)]
