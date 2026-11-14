"""The garage's GitHub client: the token file, and the REST calls it needs.

Standard library only (`urllib.request`). The token goes in the
`Authorization` header of requests to `API` and nowhere else: not in a URL,
an exception message, a log line or `repr()`. Errors carry the status and the
request path only.

Conditional requests: with `conditional=True` the last ETag seen for that URL
is sent as `If-None-Match`; GitHub answers an unchanged resource with 304,
which costs no rate limit, and the cached body is returned instead.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"


class TokenFileError(Exception):
    """The token file is missing, unsafe or empty; the message says what to do."""


def check_token_file(path: Path) -> None:
    path = Path(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        raise TokenFileError(
            f"no token file at {path}: put the token in it, then chmod 600 {path}") from None
    if st.st_uid != os.getuid():
        raise TokenFileError(f"{path} is not owned by you; it must be yours alone: chmod 600 {path}")
    if st.st_mode & 0o077:
        raise TokenFileError(f"{path} is readable or writable by others: chmod 600 {path}")


def read_token(path: Path) -> str:
    check_token_file(path)
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise TokenFileError(f"{path} is empty: put the token in it")
    return token


class ApiError(Exception):
    def __init__(self, status: int, path: str, accepted_permissions: str | None = None):
        super().__init__(f"GitHub {status} on {path}")
        self.status = status
        self.path = path
        self.accepted_permissions = accepted_permissions


class RateLimited(ApiError):
    def __init__(self, status: int, path: str, retry_at: float,
                 accepted_permissions: str | None = None):
        super().__init__(status, path, accepted_permissions)
        self.retry_at = retry_at


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


PROBES = {
    "contents": ("GET", "/contents/"),
    "actions": ("GET", "/actions/runs?per_page=1"),
    "admin-read": ("GET", "/actions/runners?per_page=1"),
    "admin-write": ("POST", "/actions/runners/registration-token"),
}


class GitHub:
    def __init__(self, token: str, opener=urllib.request.urlopen, clock=time.time):
        self._token = token
        self._opener = opener
        self._clock = clock
        self._etags: dict[str, tuple[str, object]] = {}
        self.token_expiration: str | None = None

    def __repr__(self) -> str:
        return "GitHub(token=<hidden>)"

    def request(self, method: str, path: str, body: dict | None = None,
                conditional: bool = False) -> tuple[int, object]:
        url = API + path
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pitcall-garage",
        }
        cached = self._etags.get(url) if conditional else None
        if cached:
            headers["If-None-Match"] = cached[0]
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = self._opener(req, timeout=30)
        except urllib.error.HTTPError as err:
            resp = err  # an error status is read like any other response
        with resp:
            status = resp.getcode()
            got = resp.headers
            raw = resp.read()
        expiration = got.get("GitHub-Authentication-Token-Expiration")
        if expiration:
            self.token_expiration = expiration
        if status == 304 and cached:
            return 200, cached[1]
        if 200 <= status < 300:
            parsed = json.loads(raw) if raw else None
            etag = got.get("ETag")
            if conditional and etag:
                self._etags[url] = (etag, parsed)
            return status, parsed
        accepted = got.get("X-Accepted-GitHub-Permissions")
        if status in (403, 429):
            reset = _number(got.get("x-ratelimit-reset"))
            if got.get("x-ratelimit-remaining") == "0" and reset is not None:
                raise RateLimited(status, path, reset, accepted)
            after = _number(got.get("retry-after"))
            if after is not None:
                raise RateLimited(status, path, self._clock() + after, accepted)
        raise ApiError(status, path, accepted)

    def _get_or_none(self, path: str, conditional: bool = False):
        try:
            return self.request("GET", path, conditional=conditional)[1]
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    # --- endpoints -----------------------------------------------------------

    def repo_info(self, repo: str) -> dict:
        return self.request("GET", f"/repos/{repo}", conditional=True)[1]

    def _contents(self, repo: str, path: str, ref: str):
        quoted = urllib.parse.quote(path, safe="/")
        ref_q = urllib.parse.quote(ref, safe="")
        return self._get_or_none(f"/repos/{repo}/contents/{quoted}?ref={ref_q}", conditional=True)

    def file_text(self, repo: str, path: str, ref: str) -> str | None:
        body = self._contents(repo, path, ref)
        if not isinstance(body, dict) or "content" not in body:
            return None
        return base64.b64decode(body["content"]).decode("utf-8")

    def list_dir(self, repo: str, path: str, ref: str) -> list[str] | None:
        body = self._contents(repo, path, ref)
        if not isinstance(body, list):
            return None
        return [entry["name"] for entry in body]

    def active_jobs(self, repo: str) -> list[dict]:
        jobs: list[dict] = []
        used: set[str] = set()
        for status in ("queued", "in_progress"):
            _, runs = self.request(
                "GET", f"/repos/{repo}/actions/runs?status={status}&per_page=100", conditional=True)
            for run in (runs or {}).get("workflow_runs", []):
                path = f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100"
                used.add(API + path)
                _, body = self.request("GET", path, conditional=True)
                jobs.extend((body or {}).get("jobs", []))
        # Forget finished runs' cached job lists, or the cache grows for as long as the service runs.
        prefix = f"{API}/repos/{repo}/actions/runs/"
        for url in [u for u in self._etags if u.startswith(prefix) and "/jobs?" in u and u not in used]:
            del self._etags[url]
        return jobs

    def runners(self, repo: str) -> list[dict]:
        found: list[dict] = []
        page = 1
        while True:
            _, body = self.request("GET", f"/repos/{repo}/actions/runners?per_page=100&page={page}")
            batch = (body or {}).get("runners", [])
            found.extend(batch)
            if len(batch) < 100:
                return found
            page += 1

    def runner(self, repo: str, runner_id: int) -> dict | None:
        return self._get_or_none(f"/repos/{repo}/actions/runners/{runner_id}")

    def delete_runner(self, repo: str, runner_id: int) -> bool:
        try:
            self.request("DELETE", f"/repos/{repo}/actions/runners/{runner_id}")
        except ApiError as exc:
            if exc.status == 404:
                return True
            if exc.status == 422:  # GitHub refuses to delete a runner that is running a job
                return False
            raise
        return True

    def jit_config(self, repo: str, name: str, labels) -> tuple[int, str]:
        _, body = self.request("POST", f"/repos/{repo}/actions/runners/generate-jitconfig", {
            "name": name, "runner_group_id": 1, "labels": list(labels), "work_folder": "_work"})
        return body["runner"]["id"], body["encoded_jit_config"]

    def probe(self, repo: str, permission: str) -> ApiError | None:
        method, suffix = PROBES[permission]
        try:
            self.request(method, f"/repos/{repo}{suffix}")
        except ApiError as exc:
            return exc
        return None
