"""Download this run's artifacts whose names match a pattern, each into its own directory.

The same layout actions/download-artifact gives with `pattern:` (one directory per
artifact, named after it), fetched one request at a time with retries. The cheese-ci
pool reaches GitHub through a link that resets a connection now and then; the action
lists and downloads a dozen artifacts as one call, so one reset fails the whole step,
and retrying the step retries every request with it (2026-10-24: eight whole-step
attempts in a row failed on one `ListArtifacts: ECONNRESET` each). Here each request
is retried on its own.

Standard library only: the pool's boxes have python3, git and curl, not gh or jq.

    python3 download_artifacts.py PATTERN DEST

Reads GITHUB_TOKEN (needs `actions: read`), GITHUB_REPOSITORY, GITHUB_RUN_ID and
GITHUB_API_URL from the job's environment.
"""

from __future__ import annotations

import fnmatch
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ATTEMPTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # The zip endpoint answers 302 to a signed blob URL that must be fetched WITHOUT
    # the GitHub token (urllib would forward the Authorization header).
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _with_retries(what: str, fetch):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fetch()
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == ATTEMPTS:
                raise
            delay = 2**attempt
            print(f"{what}: {error}; retrying in {delay}s ({attempt}/{ATTEMPTS})", flush=True)
            time.sleep(delay)
    raise AssertionError("unreachable")


def _api(path: str) -> urllib.request.Request:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    return urllib.request.Request(
        base + path,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def list_artifacts(repo: str, run_id: str) -> list[dict]:
    artifacts: list[dict] = []
    page = 1
    while True:
        path = f"/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100&page={page}"
        body = _with_retries(
            f"list page {page}", lambda: json.load(urllib.request.urlopen(_api(path), timeout=30))
        )
        artifacts.extend(body["artifacts"])
        if len(body["artifacts"]) < 100:
            return artifacts
        page += 1


def _zip_bytes(repo: str, artifact_id: int) -> bytes:
    request = _api(f"/repos/{repo}/actions/artifacts/{artifact_id}/zip")
    try:
        _opener.open(request, timeout=30)
    except urllib.error.HTTPError as redirect:
        if redirect.code not in (301, 302, 303, 307, 308):
            raise
        location = redirect.headers["Location"]
    else:
        raise RuntimeError(f"artifact {artifact_id}: expected a redirect to the blob")
    with urllib.request.urlopen(location, timeout=120) as response:
        return response.read()


def main(pattern: str, dest: str) -> int:
    repo, run_id = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    wanted = [
        a
        for a in list_artifacts(repo, run_id)
        if fnmatch.fnmatchcase(a["name"], pattern) and not a.get("expired")
    ]
    if not wanted:
        print(f"no artifact of run {run_id} matches {pattern}", flush=True)
        return 1
    root = Path(dest)
    for artifact in sorted(wanted, key=lambda a: a["name"]):
        data = _with_retries(artifact["name"], lambda: _zip_bytes(repo, artifact["id"]))
        target = root / artifact["name"]
        target.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(target)
        print(f"{artifact['name']}: {len(data)} bytes", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
