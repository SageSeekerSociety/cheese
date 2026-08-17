"""What a red check-run actually tells 芝士 (CI失败要把日志送到芝士眼前).

`check_state`'s failure tail used to be `"shell-tests: failure"` and nothing
more, so every CI failure cost a round trip before the fixing could start.
These tests drive the real `HttpxGitHubPrClient` through a mocked transport —
the check-runs payloads are shaped after real ones pulled from this repo on
2026-08-11, including the two surprises that decide the design: Actions
leaves `output.summary` null, and its `##[error]` line is the useless
`Process completed with exit code 1.` rather than the failure itself.
"""

import httpx
import pytest

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
        # Which app posted it. Not decoration: only github-actions runs are the
        # CI this platform gates on, so this is what decides whether a run is
        # looked at at all.
        "app": {"slug": "github-actions"},
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
    asking the jobs API about one just 404s.

    Since 2026-08-17 the guarantee is stronger than "don't ask the jobs API":
    a stranger's red check-run does not make the ref red at all. PR #506 had
    every Actions check green and only `copilot-pull-request-reviewer` red;
    the platform read that as CI failing and summoned 芝士 to fix a review
    bot's opinion, which no commit of its can turn green. GitHub itself called
    that PR `clean`."""
    recorder = _Recorder(
        runs=[
            _check_run(name="test", conclusion="success"),
            _check_run(
                name="codecov/patch",
                html_url="https://app.codecov.io/gh/acme/widgets/pull/7",
                details_url="https://app.codecov.io/gh/acme/widgets/pull/7",
                output={"title": "60% of diff hit", "summary": "target 80%"},
                app={"slug": "codecov"},
            ),
        ]
    )
    state, tail = await _check(recorder)

    assert state == "success"
    assert not any("/actions/jobs/" in url for url in recorder.urls())
    assert "codecov" not in tail
    assert "60% of diff hit" not in tail


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
