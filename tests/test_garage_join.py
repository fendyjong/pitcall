"""garage join and garage leave: what they refuse, what they print, what they write.

The GitHub client is a fake with the four methods join uses, so nothing here touches the
network, and the machine config is a file in tmp_path.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "garage"))
import garage_github  # noqa: E402
import garage_join  # noqa: E402

ADVICE = ("Repository access: add octo/app. Repository permissions: Administration read "
          "and write, Actions read, Contents read")

CI = """\
name: ci
on: push
jobs:
  test:
    runs-on: [self-hosted, linux]
    steps: []
  big:
    runs-on:
      - self-hosted
      - linux
      - large
  hosted:
    runs-on: ubuntu-latest
  matrix:
    runs-on: ${{ matrix.os }}
"""


def api_error(status, path="/repos/octo/app", accepted=None):
    return garage_github.ApiError(status, path, accepted)


class FakeApi:
    def __init__(self, private=True, missing=(), workflows=None, branch="trunk",
                 info_error=None, empty=False):
        self.private, self.missing, self.branch = private, set(missing), branch
        self.empty = empty
        self.workflows = {"ci.yml": CI} if workflows is None else workflows
        self.info_error = info_error
        self.calls = []

    def repo_info(self, repo):
        self.calls.append(("repo_info", repo))
        if self.info_error:
            raise self.info_error
        return {"private": self.private, "default_branch": self.branch}

    def probe(self, repo, permission):
        self.calls.append(("probe", permission))
        if self.empty and permission == "contents":
            return api_error(404, f"/repos/{repo}/contents/")
        if permission not in self.missing:
            return None
        accepted = "administration=write" if permission == "admin-write" else None
        return api_error(403, f"/repos/{repo}/{permission}", accepted)

    def list_dir(self, repo, path, ref):
        self.calls.append(("list_dir", path, ref))
        return [*self.workflows, "README.md"]

    def file_text(self, repo, path, ref):
        self.calls.append(("file_text", path, ref))
        text = self.workflows[path.rsplit("/", 1)[1]]
        if isinstance(text, Exception):
            raise text
        return text


def write_config(tmp_path, repos=None):
    path = tmp_path / "garage.json"
    path.write_text(json.dumps({
        "runners": 2,
        "recipe": {"start": "s", "stop": "t", "exec": "e", "cleanup": "c"},
        "token_file": str(tmp_path / "token"),
        "repos": repos or {},
    }))
    return path


def join(tmp_path, api, labels_arg=None, allow_public=False, yes=False):
    path = write_config(tmp_path)
    lines = []
    code = garage_join.run_join(api, "octo/app", path, labels_arg, allow_public, yes,
                                out=lines.append)
    return code, "\n".join(lines), json.loads(path.read_text())["repos"]


def test_labels_are_parsed_into_sets():
    assert garage_join.parse_labels("self-hosted,linux; self-hosted , large;") == [
        ["self-hosted", "linux"], ["self-hosted", "large"]]


def test_a_labels_argument_naming_no_set_is_refused():
    with pytest.raises(ValueError):
        garage_join.parse_labels(" ; , ")


def test_the_trust_line_is_the_spec_s_words():
    assert garage_join.TRUST_LINE == (
        "A joined repo is trusted like your own code: its jobs share runners and caches "
        "with every other joined repo's jobs.")


def test_found_sets_are_listed_and_nothing_is_written_without_yes(tmp_path):
    api = FakeApi()
    code, text, repos = join(tmp_path, api)
    assert code == 0
    lines = text.splitlines()
    found = lines.index("Label sets that include self-hosted in octo/app's workflows on trunk:")
    assert lines[found + 1:found + 3] == ["  - self-hosted, linux", "  - self-hosted, linux, large"]
    assert "ubuntu-latest" not in text
    assert ".github/workflows/ci.yml:15" in text
    assert garage_join.TRUST_LINE in text
    assert "Nothing written. Re-run with --yes to join octo/app with these label sets." in text
    assert repos == {}


def test_workflows_are_read_from_the_default_branch_only(tmp_path):
    api = FakeApi(branch="trunk")
    join(tmp_path, api)
    reads = [c for c in api.calls if c[0] in ("list_dir", "file_text")]
    assert reads and all(c[2] == "trunk" for c in reads)
    assert ("file_text", ".github/workflows/README.md", "trunk") not in api.calls


def test_yes_writes_the_found_sets(tmp_path):
    code, text, repos = join(tmp_path, FakeApi(), yes=True)
    assert code == 0
    assert "Joined octo/app." in text
    assert repos == {"octo/app": {"labels": [["self-hosted", "linux"],
                                             ["self-hosted", "linux", "large"]]}}


def test_a_labels_argument_replaces_the_found_sets(tmp_path):
    api = FakeApi()
    code, _, repos = join(tmp_path, api, labels_arg="self-hosted,gpu", yes=True)
    assert code == 0
    assert repos == {"octo/app": {"labels": [["self-hosted", "gpu"]]}}
    assert not [c for c in api.calls if c[0] == "list_dir"]


def test_an_invalid_labels_argument_is_refused(tmp_path):
    code, _, repos = join(tmp_path, FakeApi(), labels_arg=" ; ", yes=True)
    assert code == 1
    assert repos == {}


def test_a_public_repo_is_refused_without_allow_public(tmp_path):
    api = FakeApi(private=False)
    code, text, repos = join(tmp_path, api, yes=True)
    assert code == 1
    assert garage_join.PUBLIC_RISK in text
    assert "--allow-public" in text
    assert repos == {}
    assert not [c for c in api.calls if c[0] == "probe"]


def test_allow_public_still_warns_and_advises(tmp_path):
    code, text, repos = join(tmp_path, FakeApi(private=False), allow_public=True, yes=True)
    assert code == 0
    assert garage_join.PUBLIC_RISK in text
    assert garage_join.PUBLIC_ADVICE in text
    assert "octo/app" in repos


def test_a_missing_permission_is_named_with_the_exact_advice(tmp_path):
    api = FakeApi(missing={"admin-write"})
    code, text, repos = join(tmp_path, api, yes=True)
    assert code == 1
    assert "Administration write" in text
    assert "administration=write" in text
    assert ADVICE in text
    assert repos == {}
    assert not [c for c in api.calls if c[0] == "list_dir"]


def test_every_missing_permission_is_named(tmp_path):
    api = FakeApi(missing={"contents", "actions", "admin-read", "admin-write"})
    code, text, _ = join(tmp_path, api)
    assert code == 1
    for name in ("Contents read", "Actions read", "Administration read", "Administration write"):
        assert name in text


def test_coverage_checks_all_four_permissions(tmp_path):
    api = FakeApi()
    join(tmp_path, api)
    assert [c[1] for c in api.calls if c[0] == "probe"] == [
        "contents", "actions", "admin-read", "admin-write"]


def test_an_empty_repo_is_not_a_missing_contents_permission(tmp_path):
    code, text, _ = join(tmp_path, FakeApi(empty=True))
    assert code == 0
    assert "Contents read" not in text


def test_a_network_failure_is_one_line_and_writes_nothing(tmp_path):
    code, text, repos = join(tmp_path, FakeApi(info_error=OSError("network is unreachable")),
                             yes=True)
    assert code == 1
    assert text == "garage join: network is unreachable; nothing written."
    assert repos == {}


def test_a_repo_the_token_cannot_see_is_refused(tmp_path):
    code, text, repos = join(tmp_path, FakeApi(info_error=api_error(404)), yes=True)
    assert code == 1
    assert ADVICE in text
    assert repos == {}


def test_without_yes_and_no_label_set_nothing_is_written(tmp_path):
    api = FakeApi(workflows={"ci.yml": "jobs:\n  a:\n    runs-on: ubuntu-latest\n"})
    code, text, repos = join(tmp_path, api)
    assert code == 0
    assert "Nothing written." in text and "--labels" in text
    assert repos == {}


def test_yes_with_no_label_set_refuses(tmp_path):
    api = FakeApi(workflows={"ci.yml": "jobs:\n  a:\n    runs-on: ubuntu-latest\n"})
    code, _, repos = join(tmp_path, api, yes=True)
    assert code == 1
    assert repos == {}


def test_leave_removes_a_joined_repo(tmp_path):
    path = write_config(tmp_path, repos={"octo/app": {"labels": [["self-hosted"]]}})
    lines = []
    assert garage_join.run_leave("octo/app", path, out=lines.append) == 0
    assert lines == ["Left octo/app. A job already running for it finishes; "
                     "nothing new is registered for it."]
    assert json.loads(path.read_text())["repos"] == {}


def test_leave_on_a_repo_not_joined_says_so(tmp_path):
    path = write_config(tmp_path)
    lines = []
    assert garage_join.run_leave("octo/app", path, out=lines.append) == 0
    assert lines == ["octo/app was not joined."]


def test_a_workflow_file_that_is_not_utf8_is_listed_as_unreadable(tmp_path):
    bad = garage_github.ContentError(".github/workflows/legacy.yml on trunk is not UTF-8")
    api = FakeApi(workflows={"ci.yml": CI, "legacy.yml": bad})
    code, text, repos = join(tmp_path, api)
    assert code == 0 and repos == {}
    assert "  - .github/workflows/legacy.yml: not UTF-8" in text.splitlines()
    assert "a runner group, or a file that is not UTF-8)" in text  # the list's heading says so
    assert "  - self-hosted, linux, large" in text.splitlines()  # the readable file still counts
