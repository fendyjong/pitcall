"""The garage's GitHub client (Task 4). No network: every response is scripted."""

import base64
import io
import json
import os
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "garage"))
import garage_github  # noqa: E402
from garage_github import ApiError, GitHub, RateLimited, TokenFileError  # noqa: E402

TOKEN = "test-token-not-real"


def headers(**values):
    msg = Message()
    for name, value in values.items():
        msg[name.replace("_", "-")] = str(value)
    return msg


class Response:
    def __init__(self, status, body=None, **hdrs):
        self.status, self.hdrs = status, hdrs
        self.raw = b"" if body is None else json.dumps(body).encode()

    def getcode(self):
        return self.status

    @property
    def headers(self):
        return headers(**self.hdrs)

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Answers each request with the next scripted Response, recording the request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        resp = self.responses.pop(0)
        if resp.status >= 300:
            raise urllib.error.HTTPError(req.full_url, resp.status, "scripted",
                                         resp.headers, io.BytesIO(resp.raw))
        return resp


def client(*responses, now=1000.0):
    opener = Opener(*responses)
    return GitHub(TOKEN, opener=opener, clock=lambda: now), opener


# --- token file ---------------------------------------------------------------

def test_a_private_token_file_is_read_and_stripped(tmp_path):
    path = tmp_path / "garage.token"
    path.write_text("  test-token-not-real\n")
    path.chmod(0o600)
    assert garage_github.read_token(path) == "test-token-not-real"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_a_token_file_others_can_reach_is_refused(tmp_path, mode):
    path = tmp_path / "garage.token"
    path.write_text("x")
    path.chmod(mode)
    with pytest.raises(TokenFileError) as err:
        garage_github.read_token(path)
    assert f"chmod 600 {path}" in str(err.value)


def test_a_token_file_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "garage.token"
    path.write_text("x")
    path.chmod(0o600)
    someone_else = os.getuid() + 1
    monkeypatch.setattr(garage_github.os, "getuid", lambda: someone_else)
    with pytest.raises(TokenFileError, match="not owned by you"):
        garage_github.check_token_file(path)


def test_a_missing_or_empty_token_file_is_refused(tmp_path):
    with pytest.raises(TokenFileError, match="no token file"):
        garage_github.read_token(tmp_path / "absent")
    empty = tmp_path / "empty"
    empty.write_text("\n")
    empty.chmod(0o600)
    with pytest.raises(TokenFileError, match="empty"):
        garage_github.read_token(empty)


# --- requests -----------------------------------------------------------------

def test_a_request_goes_to_api_github_com_with_the_token_in_the_header_only():
    gh, opener = client(Response(200, {"private": True, "default_branch": "main"}))
    assert gh.repo_info("octo/app") == {"private": True, "default_branch": "main"}
    req = opener.requests[0]
    assert req.full_url == "https://api.github.com/repos/octo/app"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert req.get_header("X-github-api-version") == "2022-11-28"
    assert TOKEN not in req.full_url
    assert TOKEN not in repr(gh)


def test_a_conditional_request_sends_the_etag_and_reuses_the_body_on_304():
    gh, opener = client(Response(200, {"default_branch": "main"}, ETag='"v1"'), Response(304))
    assert gh.repo_info("octo/app") == {"default_branch": "main"}
    assert gh.repo_info("octo/app") == {"default_branch": "main"}
    assert opener.requests[0].get_header("If-none-match") is None
    assert opener.requests[1].get_header("If-none-match") == '"v1"'


def test_an_error_names_status_and_path_but_never_the_token():
    gh, _ = client(Response(404, {"message": "Not Found"},
                            X_Accepted_GitHub_Permissions="administration=read"))
    with pytest.raises(ApiError) as err:
        gh.request("GET", "/repos/octo/app/actions/runners")
    assert str(err.value) == "GitHub 404 on /repos/octo/app/actions/runners"
    assert err.value.status == 404
    assert err.value.accepted_permissions == "administration=read"
    assert TOKEN not in str(err.value) and TOKEN not in repr(err.value)


def test_a_primary_rate_limit_waits_until_the_reset_time():
    gh, _ = client(Response(403, {}, x_ratelimit_remaining=0, x_ratelimit_reset=5000))
    with pytest.raises(RateLimited) as err:
        gh.request("GET", "/repos/octo/app")
    assert err.value.retry_at == 5000.0


def test_a_secondary_rate_limit_honours_retry_after():
    gh, _ = client(Response(429, {}, retry_after=60), now=1000.0)
    with pytest.raises(RateLimited) as err:
        gh.request("GET", "/repos/octo/app")
    assert err.value.retry_at == 1060.0


def test_a_403_that_is_not_a_rate_limit_is_a_plain_error():
    gh, _ = client(Response(403, {}, x_ratelimit_remaining=4999))
    with pytest.raises(ApiError) as err:
        gh.request("GET", "/repos/octo/app")
    assert not isinstance(err.value, RateLimited)
    assert err.value.status == 403


def test_the_token_expiration_header_is_remembered():
    gh, _ = client(Response(200, {}, GitHub_Authentication_Token_Expiration="2026-12-01 00:00:00 UTC"))
    gh.repo_info("octo/app")
    assert gh.token_expiration == "2026-12-01 00:00:00 UTC"


# --- endpoints ----------------------------------------------------------------

def test_file_text_decodes_the_content_and_a_missing_file_is_none():
    content = base64.encodebytes(b'{"runner": {}}\n').decode()
    gh, opener = client(Response(200, {"type": "file", "content": content}), Response(404, {}))
    assert gh.file_text("octo/app", ".pitcall/config.json", "feat/x") == '{"runner": {}}\n'
    assert opener.requests[0].full_url == (
        "https://api.github.com/repos/octo/app/contents/.pitcall/config.json?ref=feat%2Fx")
    assert gh.file_text("octo/app", ".pitcall/config.json", "main") is None


def test_list_dir_names_the_entries():
    gh, _ = client(Response(200, [{"name": "ci.yml"}, {"name": "docs.yaml"}]), Response(404, {}))
    assert gh.list_dir("octo/app", ".github/workflows", "main") == ["ci.yml", "docs.yaml"]
    assert gh.list_dir("octo/app", ".github/workflows", "main") is None


def test_active_jobs_reads_queued_and_in_progress_runs():
    gh, opener = client(
        Response(200, {"workflow_runs": [{"id": 7}]}),
        Response(200, {"jobs": [{"id": 70, "status": "queued", "labels": ["self-hosted"]}]}),
        Response(200, {"workflow_runs": [{"id": 8}]}),
        Response(200, {"jobs": [{"id": 80, "status": "in_progress", "labels": ["linux"]}]}),
    )
    assert [job["id"] for job in gh.active_jobs("octo/app")] == [70, 80]
    assert [r.full_url.removeprefix("https://api.github.com") for r in opener.requests] == [
        "/repos/octo/app/actions/runs?status=queued&per_page=100",
        "/repos/octo/app/actions/runs/7/jobs?per_page=100",
        "/repos/octo/app/actions/runs?status=in_progress&per_page=100",
        "/repos/octo/app/actions/runs/8/jobs?per_page=100",
    ]


def test_a_finished_runs_cached_jobs_are_forgotten():
    gh, _ = client(
        Response(200, {"workflow_runs": [{"id": 7}]}, ETag='"a"'),
        Response(200, {"jobs": []}, ETag='"b"'),
        Response(200, {"workflow_runs": []}),
        Response(200, {"workflow_runs": []}, ETag='"c"'),
        Response(200, {"workflow_runs": []}),
    )
    gh.active_jobs("octo/app")
    assert any("/runs/7/jobs" in url for url in gh._etags)
    gh.active_jobs("octo/app")
    assert not any("/runs/7/jobs" in url for url in gh._etags)


def test_runners_follows_pages_until_a_short_one():
    full = {"runners": [{"id": n} for n in range(100)]}
    gh, opener = client(Response(200, full), Response(200, {"runners": [{"id": 100}]}))
    assert len(gh.runners("octo/app")) == 101
    assert opener.requests[1].full_url.endswith("per_page=100&page=2")


def test_runner_is_none_once_github_has_removed_it():
    gh, _ = client(Response(200, {"id": 5, "busy": True}), Response(404, {}))
    assert gh.runner("octo/app", 5) == {"id": 5, "busy": True}
    assert gh.runner("octo/app", 5) is None


@pytest.mark.parametrize("status, result", [(204, True), (404, True), (422, False)])
def test_delete_runner(status, result):
    gh, opener = client(Response(status))
    assert gh.delete_runner("octo/app", 5) is result
    assert opener.requests[0].get_method() == "DELETE"


def test_delete_runner_raises_on_other_errors():
    gh, _ = client(Response(500, {}))
    with pytest.raises(ApiError):
        gh.delete_runner("octo/app", 5)


def test_jit_config_registers_with_the_exact_labels():
    gh, opener = client(Response(201, {"runner": {"id": 42}, "encoded_jit_config": "ZW5j"}))
    assert gh.jit_config("octo/app", "pitcall-3f9c2a71-r1-1", ["self-hosted", "Linux"]) == (42, "ZW5j")
    req = opener.requests[0]
    assert req.full_url.endswith("/repos/octo/app/actions/runners/generate-jitconfig")
    assert json.loads(req.data) == {"name": "pitcall-3f9c2a71-r1-1", "runner_group_id": 1,
                                    "labels": ["self-hosted", "Linux"], "work_folder": "_work"}


def test_probe_returns_the_error_instead_of_raising():
    gh, opener = client(Response(201, {"token": "x"}), Response(403, {}))
    assert gh.probe("octo/app", "admin-write") is None
    assert opener.requests[0].get_method() == "POST"
    assert opener.requests[0].full_url.endswith("/actions/runners/registration-token")
    err = gh.probe("octo/app", "admin-read")
    assert isinstance(err, ApiError) and err.status == 403
