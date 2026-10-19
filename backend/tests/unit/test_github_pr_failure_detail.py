"""What a red check-run actually tells 芝士 (CI失败要把日志送到芝士眼前).

`check_state`'s failure tail used to be `"shell-tests: failure"` and nothing
more, so every CI failure cost a round trip before the fixing could start.
These tests drive the real `HttpxGitHubPrClient` through a mocked transport —
the check-runs payloads are shaped after real ones pulled from this repo on
2026-08-11, including the two surprises that decide the design: Actions
leaves `output.summary` null, and its `##[error]` line is the useless
`Process completed with exit code 1.` rather than the failure itself.
"""

import json
import time
from collections import OrderedDict

import httpx
import pytest

from app.core import forge_etags, forge_quota
from app.domain.review.github_pr import HttpxGitHubPrClient

_JOB_ID = 93711698702
_JOB_URL = f"https://github.com/acme/widgets/actions/runs/31470222386/job/{_JOB_ID}"
_LOG_URL = "https://blob.example.invalid/actions-results/job-logs.txt?sig=abc"

# Shaped after a real failing shell-tests job: the error marker says nothing,
# the line above it says everything.
_REAL_SHAPED_LOG = "\n".join(
    [
        "2026-08-11T07:44:58.7907790Z ##[group]Run bash deploy/tests/test-x.sh",
        "2026-08-11T07:44:58.7909100Z \x1b[36;1mbash deploy/tests/test-x.sh\x1b[0m",
        "2026-08-11T07:44:58.7946365Z shell: /usr/bin/bash -e {0}",
        "2026-08-11T07:44:58.7946569Z ##[endgroup]",
        "2026-08-11T07:44:58.9052507Z FAIL: the secret file was not handed over",
        "2026-08-11T07:44:58.9065765Z ##[error]Process completed with exit code 1.",
        "2026-08-11T07:44:58.9170694Z Node 20 is being deprecated. …",
    ]
)


def _check_run(**overrides) -> dict:
    run = {
        "id": _JOB_ID,
        "name": "shell-tests",
        "status": "completed",
        "conclusion": "failure",
        "html_url": _JOB_URL,
        "details_url": _JOB_URL,
        # Actions really does leave both null — the detail lives in the log.
        "output": {"title": None, "summary": None, "annotations_count": 2},
    }
    run.update(overrides)
    return run


class _Recorder:
    """A transport that serves check-runs + a redirected log, remembering
    every request so tests can assert on what was (and wasn't) sent."""

    def __init__(self, *, runs: list[dict], log: str = _REAL_SHAPED_LOG) -> None:
        self.runs = runs
        self.log = log
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/check-runs"):
            return httpx.Response(
                200, json={"check_runs": self.runs, "total_count": len(self.runs)}
            )
        if path.endswith("/logs"):
            return httpx.Response(302, headers={"Location": _LOG_URL})
        if str(request.url) == _LOG_URL:
            return httpx.Response(200, text=self.log)
        return httpx.Response(404, json={"message": "not found"})

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _client(handler) -> HttpxGitHubPrClient:
    return HttpxGitHubPrClient(transport=httpx.MockTransport(handler))


async def _check(handler) -> tuple[str, str]:
    return await _client(handler).check_state(
        owner="acme", repo="widgets", ref="deadbeef", token="t"
    )


@pytest.mark.anyio
async def test_failure_tail_carries_job_name_link_and_error_lines():
    recorder = _Recorder(runs=[_check_run()])
    state, tail = await _check(recorder)

    assert state == "failure"
    # The headline stays the first line — the card's note is built from it.
    assert tail.splitlines()[0] == "shell-tests: failure"
    assert _JOB_URL in tail
    # The line that names the real failure, not just the error marker.
    assert "FAIL: the secret file was not handed over" in tail
    assert "##[error]Process completed with exit code 1." in tail
    # Timestamps and ANSI escapes are stripped — this is meant to be read.
    assert "2026-08-11T07:44:58" not in tail
    assert "\x1b[" not in tail


@pytest.mark.anyio
async def test_log_redirect_is_followed_without_replaying_the_token():
    """GitHub 302s to a pre-signed URL on a third-party host. Sending our
    installation token there would hand a GitHub credential to a storage
    provider for no reason — the pre-signed URL needs no auth of ours."""
    recorder = _Recorder(runs=[_check_run()])
    await _check(recorder)

    blob = next(r for r in recorder.requests if str(r.url) == _LOG_URL)
    assert "authorization" not in {k.lower() for k in blob.headers}
    # ...while the API call that produced the redirect obviously does carry it.
    api = next(r for r in recorder.requests if r.url.path.endswith("/logs"))
    assert api.headers["Authorization"] == "Bearer t"


@pytest.mark.anyio
async def test_log_with_no_error_marker_falls_back_to_its_tail():
    """A cancelled or timed-out job never emits `##[error]`; delivering
    nothing at all would be worse than delivering where it stopped."""
    recorder = _Recorder(
        runs=[_check_run(conclusion="timed_out")],
        log="\n".join(f"2026-08-11T07:44:58.000000{i}Z step {i}" for i in range(40)),
    )
    _, tail = await _check(recorder)

    assert "step 39" in tail
    assert "step 0" not in tail  # only the tail, not the whole log


@pytest.mark.anyio
async def test_unreachable_log_still_delivers_headline_and_link():
    """A poll tick that can't read the log must not lose the failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(
                200, json={"check_runs": [_check_run()], "total_count": 1}
            )
        return httpx.Response(500, json={"message": "boom"})

    state, tail = await _check(handler)

    assert state == "failure"
    assert tail.splitlines()[0] == "shell-tests: failure"
    assert _JOB_URL in tail


@pytest.mark.anyio
async def test_non_actions_check_run_is_not_sent_to_the_jobs_api():
    """Other apps publish check-runs too, and their ids are not job ids —
    asking the jobs API about one just 404s. Their `output` is used instead,
    which for them is actually populated."""
    recorder = _Recorder(
        runs=[
            _check_run(
                name="codecov/patch",
                html_url="https://app.codecov.io/gh/acme/widgets/pull/7",
                details_url="https://app.codecov.io/gh/acme/widgets/pull/7",
                output={"title": "60% of diff hit", "summary": "target 80%"},
            )
        ]
    )
    _, tail = await _check(recorder)

    assert not any("/actions/jobs/" in url for url in recorder.urls())
    assert "60% of diff hit" in tail
    assert "target 80%" in tail


@pytest.mark.anyio
async def test_only_the_first_few_failed_jobs_are_expanded():
    """One root cause reddening ten jobs is one investigation, and each
    expansion is another GitHub round trip on every poll."""
    runs = [
        _check_run(
            name=f"job-{i}",
            html_url=_JOB_URL.replace(str(_JOB_ID), str(_JOB_ID + i)),
            details_url=_JOB_URL.replace(str(_JOB_ID), str(_JOB_ID + i)),
        )
        for i in range(5)
    ]
    recorder = _Recorder(runs=runs)
    _, tail = await _check(recorder)

    assert len([u for u in recorder.urls() if "/actions/jobs/" in u]) == 3
    assert "另有 2 个失败的 job 未展开" in tail


@pytest.mark.anyio
async def test_token_shaped_strings_in_a_log_are_redacted():
    """Actions masks the secrets it knows about; a log can still echo one it
    was never told about (a curl of our own API, a `gh auth status`)."""
    recorder = _Recorder(
        runs=[_check_run()],
        log=(
            "2026-08-11T07:44:58.0000000Z leaked ghs_AbCdEfGhIjKlMnOpQrStUvWx1234\n"
            "2026-08-11T07:44:58.0000001Z ##[error]Process completed with exit code 1."
        ),
    )
    _, tail = await _check(recorder)

    assert "ghs_AbCdEfGhIjKlMnOpQrStUvWx1234" not in tail
    assert "<已脱敏>" in tail


@pytest.mark.anyio
async def test_green_and_pending_checks_never_touch_the_logs_api():
    """The detail fetch is a failure-only cost — the steady state is a PR
    polling green/pending every 60s, and it must stay one request."""
    recorder = _Recorder(
        runs=[
            _check_run(conclusion="success"),
            _check_run(name="e2e", status="in_progress", conclusion=None),
        ]
    )
    state, _ = await _check(recorder)

    assert state == "pending"
    assert recorder.urls() == [
        "https://api.github.com/repos/acme/widgets/commits/deadbeef/check-runs"
        "?per_page=100"
    ]


@pytest.fixture
def owned_tokens(monkeypatch):
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    monkeypatch.setattr(forge_etags, "_kept", OrderedDict())
    for token, installation in (("owned", 1), ("rotated", 1), ("other", 2)):
        forge_quota.own(token, installation, time.time() + 3600)


async def _read_failure(client, token="owned"):
    return await client.check_state(
        owner="acme", repo="widgets", ref="deadbeef", token=token
    )


def _log_requests(recorder):
    return [r for r in recorder.requests if "/actions/jobs/" in r.url.path]


@pytest.mark.anyio
async def test_repeated_failure_polls_reuse_logs_but_read_live_checks(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])
    client = _client(recorder)
    first = await _read_failure(client)
    second = await _read_failure(client)

    assert second == first
    assert len(_log_requests(recorder)) == 1
    assert (
        len([r for r in recorder.requests if r.url.path.endswith("/check-runs")]) == 2
    )


@pytest.mark.anyio
async def test_failure_logs_refresh_after_five_minutes(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])
    now = [0.0]
    client = HttpxGitHubPrClient(
        transport=httpx.MockTransport(recorder), clock=lambda: now[0]
    )
    await _read_failure(client)
    now[0] = 299
    await _read_failure(client)
    assert len(_log_requests(recorder)) == 1

    now[0] = 300
    await _read_failure(client)
    assert len(_log_requests(recorder)) == 2


@pytest.mark.anyio
async def test_a_rerun_job_reads_its_new_log_immediately(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])
    client = _client(recorder)
    await _read_failure(client)

    next_url = _JOB_URL.replace(str(_JOB_ID), str(_JOB_ID + 1))
    recorder.runs = [
        _check_run(id=_JOB_ID + 1, details_url=next_url, html_url=next_url)
    ]
    recorder.log = "FAIL: a different failure after rerunning"
    state, tail = await _read_failure(client)

    assert state == "failure"
    assert "a different failure after rerunning" in tail
    assert len(_log_requests(recorder)) == 2


@pytest.mark.anyio
async def test_failure_logs_are_not_shared_across_tokens_or_installations(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])

    def handler(request):
        if (
            "/actions/jobs/" in request.url.path
            and request.headers.get("authorization") == "Bearer rotated"
        ):
            recorder.requests.append(request)
            return httpx.Response(403)
        return recorder(request)

    client = _client(handler)
    await _read_failure(client)
    _, denied = await _read_failure(client, token="rotated")
    assert "FAIL: the secret file" not in denied
    assert len(_log_requests(recorder)) == 2

    recorder.log = "FAIL: another installation's log"
    _, tail = await _read_failure(client, token="other")
    assert "another installation's log" in tail
    assert len(_log_requests(recorder)) == 3


@pytest.mark.anyio
async def test_unowned_credentials_do_not_reuse_failure_logs(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])
    client = _client(recorder)
    await _read_failure(client, token="unowned")
    recorder.log = "FAIL: freshly authorized log"
    _, tail = await _read_failure(client, token="unowned")
    assert "freshly authorized log" in tail
    assert len(_log_requests(recorder)) == 2


@pytest.mark.anyio
async def test_an_unreachable_log_is_retried_on_the_next_poll(owned_tokens):
    recorder = _Recorder(runs=[_check_run()])
    failed = [True]

    def handler(request):
        if "/actions/jobs/" in request.url.path and failed[0]:
            recorder.requests.append(request)
            return httpx.Response(500)
        return recorder(request)

    client = _client(handler)
    _, first = await _read_failure(client)
    assert "FAIL: the secret file" not in first
    failed[0] = False
    _, second = await _read_failure(client)
    assert "FAIL: the secret file" in second
    assert len(_log_requests(recorder)) == 2


class _MeteredRecorder(_Recorder):
    """A stable GitHub that charges authenticated non-304 API responses."""

    def __init__(self):
        runs = []
        for i in range(3):
            url = _JOB_URL.replace(str(_JOB_ID), str(_JOB_ID + i))
            runs.append(_check_run(id=_JOB_ID + i, details_url=url, html_url=url))
        super().__init__(runs=runs)
        self.api_reads = 0
        self.charged_reads = 0
        self.not_modified = 0

    def __call__(self, request):
        if request.url.host != "api.github.com":
            return super().__call__(request)
        self.api_reads += 1
        path = request.url.path
        if path.endswith("/check-runs") or path.endswith("/actions/runs"):
            self.requests.append(request)
            etag = '"stable"'
            if request.headers.get("if-none-match") == etag:
                self.not_modified += 1
                return httpx.Response(304, headers={"etag": etag})
            body = (
                {"check_runs": self.runs, "total_count": len(self.runs)}
                if path.endswith("/check-runs")
                else {"workflow_runs": [], "total_count": 0}
            )
            response = httpx.Response(200, json=body, headers={"etag": etag})
        else:
            response = super().__call__(request)
        self.charged_reads += 1
        return response


@pytest.mark.anyio
async def test_one_hour_of_failure_polling_has_a_counted_request_budget(owned_tokens):
    recorder = _MeteredRecorder()
    now = [0.0]
    client = HttpxGitHubPrClient(
        transport=httpx.MockTransport(recorder), clock=lambda: now[0]
    )
    polls = 0
    for second in range(0, 3600, 60):
        now[0] = second
        state, tail = await _read_failure(client)
        assert state == "failure" and "FAIL: the secret file" in tail
        polls += 1

    counts = {
        "polls": polls,
        "api_http_reads": recorder.api_reads,
        "primary_charged_reads": recorder.charged_reads,
        "not_modified": recorder.not_modified,
        "job_log_reads": len(_log_requests(recorder)),
    }
    print(json.dumps(counts, sort_keys=True))
    assert counts["job_log_reads"] == 36
    assert counts["primary_charged_reads"] == 38
    assert counts["not_modified"] == 118
    assert counts["api_http_reads"] == 156


@pytest.mark.anyio
async def test_an_empty_log_is_not_kept_while_uploading_finishes(owned_tokens):
    recorder = _Recorder(runs=[_check_run()], log="")
    client = _client(recorder)
    _, first = await _read_failure(client)
    assert "FAIL: the upload is ready" not in first

    recorder.log = "FAIL: the upload is ready"
    _, second = await _read_failure(client)
    assert "FAIL: the upload is ready" in second
    assert len(_log_requests(recorder)) == 2


@pytest.mark.anyio
async def test_failure_log_storage_evicts_older_jobs_at_capacity(
    owned_tokens, monkeypatch
):
    from app.domain.review.github_pr import httpx_client

    monkeypatch.setattr(httpx_client, "_FAILURE_LOG_CACHE_ENTRIES", 2)
    recorder = _Recorder(runs=[])
    client = _client(recorder)
    for i in (0, 1, 2, 0):
        url = _JOB_URL.replace(str(_JOB_ID), str(_JOB_ID + i))
        recorder.runs = [_check_run(id=_JOB_ID + i, details_url=url, html_url=url)]
        await _read_failure(client)

    assert len(_log_requests(recorder)) == 4
