"""The 两阶段采纳 (PR迭代式) client, `HttpxGitHubPrClient` — specifically
`check_state`'s handling of "zero check-runs" (see `github_pr.py`'s
`_resolve_zero_checks` docstring for the full reasoning).

These tests drive the real `HttpxGitHubPrClient.check_state` through a mocked
HTTP transport — never a monkeypatched `check_state` — so the actual
check-runs/check-suites parsing and decision logic runs for real. This is the
gap the bug (#209: a docs-only PR stuck forever in `pr_open`) slipped through:
the only other coverage of this poller path fakes `check_state` itself away
(see `tests/integration/test_accept_pr.py::FakeGitHubPrClient`), so a bug in
how the tri-state is *derived* from raw GitHub payloads was invisible to it.
"""

import httpx
import pytest

from app.domain.review.github_pr import GitHubPrError, HttpxGitHubPrClient


def _check_runs_response(runs: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"check_runs": runs, "total_count": len(runs)})


def _check_suites_response(suites: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"check_suites": suites, "total_count": len(suites)}
    )


def _suite(slug: str, status: str = "queued") -> dict:
    return {"app": {"slug": slug}, "status": status, "conclusion": None}


class _FakeClock:
    """A controllable monotonic clock — lets tests cross the grace-period
    boundary without a real sleep."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _client(
    handler, *, grace_s: float = 120.0, clock: _FakeClock | None = None
) -> HttpxGitHubPrClient:
    return HttpxGitHubPrClient(
        transport=httpx.MockTransport(handler),
        zero_checks_grace_s=grace_s,
        clock=clock or _FakeClock(),
    )


def _routed(*, check_runs: httpx.Response, check_suites: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            return check_runs
        if request.url.path.endswith("/check-suites"):
            return check_suites
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


# ---- non-empty check-runs: existing pending/failure/success semantics untouched --


@pytest.mark.anyio
async def test_real_checks_success_when_all_completed_ok():
    handler = _routed(
        check_runs=_check_runs_response(
            [
                {"name": "test", "status": "completed", "conclusion": "success"},
                {"name": "lint", "status": "completed", "conclusion": "neutral"},
            ]
        ),
        check_suites=_check_suites_response([]),  # must not even be called
    )
    state, tail = await _client(handler).check_state(
        owner="acme", repo="widgets", ref="deadbeef", token="t"
    )
    assert state == "success"
    assert "2" in tail


@pytest.mark.anyio
async def test_real_checks_pending_when_one_still_running():
    handler = _routed(
        check_runs=_check_runs_response(
            [
                {"name": "test", "status": "completed", "conclusion": "success"},
                {"name": "e2e", "status": "in_progress", "conclusion": None},
            ]
        ),
        check_suites=_check_suites_response([]),
    )
    state, tail = await _client(handler).check_state(
        owner="acme", repo="widgets", ref="deadbeef", token="t"
    )
    assert state == "pending"
    assert "e2e" in tail


@pytest.mark.anyio
async def test_real_checks_failure_wins_even_with_others_still_running():
    handler = _routed(
        check_runs=_check_runs_response(
            [
                {"name": "test", "status": "completed", "conclusion": "failure"},
                {"name": "e2e", "status": "in_progress", "conclusion": None},
            ]
        ),
        check_suites=_check_suites_response([]),
    )
    state, tail = await _client(handler).check_state(
        owner="acme", repo="widgets", ref="deadbeef", token="t"
    )
    assert state == "failure"
    assert "test" in tail


# ---- zero check-runs: the ambiguous branch this fix resolves ------------------


@pytest.mark.anyio
async def test_zero_checks_with_github_actions_suite_pending_stays_pending():
    """Case 2 from the brief: a workflow DID match (a github-actions suite
    exists, e.g. freshly created for a just-pushed commit) but no check-run
    has been created yet. Must never flip to success, no matter how long."""
    handler = _routed(
        check_runs=_check_runs_response([]),
        check_suites=_check_suites_response([_suite("github-actions", "queued")]),
    )
    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="fresh", token="t"
    )
    assert state == "pending"

    # Even well past the grace period, a live github-actions suite keeps it pending.
    clock.advance(10_000)
    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="fresh", token="t"
    )
    assert state == "pending"


@pytest.mark.anyio
async def test_zero_checks_no_suite_at_all_stays_pending_within_grace():
    """First sighting of "nothing at all" must not immediately succeed —
    that's exactly the bug (#209) this fix closes."""
    handler = _routed(
        check_runs=_check_runs_response([]), check_suites=_check_suites_response([])
    )
    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, tail = await client.check_state(
        owner="acme", repo="widgets", ref="docsonly", token="t"
    )
    assert state == "pending"
    assert "还没有检查报告" == tail

    clock.advance(60.0)  # still inside the 120s grace window
    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="docsonly", token="t"
    )
    assert state == "pending"


@pytest.mark.anyio
async def test_zero_checks_no_suite_past_grace_becomes_success():
    """Case 1 from the brief: paths-ignore skipped every workflow — this ref
    will genuinely never get a check. Once the grace period has elapsed with
    the "zero, no suite" state holding, it's safe to call it success."""
    handler = _routed(
        check_runs=_check_runs_response([]), check_suites=_check_suites_response([])
    )
    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="docsonly", token="t"
    )
    assert state == "pending"  # first sighting

    clock.advance(120.0)
    state, tail = await client.check_state(
        owner="acme", repo="widgets", ref="docsonly", token="t"
    )
    assert state == "success"
    assert "不会" not in tail or "workflow" in tail  # sanity: message mentions workflow


@pytest.mark.anyio
async def test_zero_checks_third_party_suite_only_is_not_mistaken_for_github_actions():
    """The real shape observed on PR #209: codecov opens (and never closes) a
    check-suite on every commit even when no CI workflow runs. That suite
    must NOT be read as "a workflow matched" — only a github-actions suite
    counts."""
    handler = _routed(
        check_runs=_check_runs_response([]),
        check_suites=_check_suites_response([_suite("codecov", "queued")]),
    )
    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="pr209", token="t"
    )
    assert state == "pending"

    clock.advance(120.0)
    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="pr209", token="t"
    )
    assert state == "success"


@pytest.mark.anyio
async def test_zero_checks_suite_appearing_later_resets_the_grace_clock():
    """A github-actions suite that shows up mid-grace-period (a slightly
    slower-than-usual webhook) must cancel the "will never check" verdict —
    not just delay it, since the clock entry is dropped and would otherwise
    let a *later* unrelated zero-check sighting reuse a stale first-seen
    time."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-runs"):
            return _check_runs_response([])
        calls["n"] += 1
        if calls["n"] == 1:
            return _check_suites_response([])
        return _check_suites_response([_suite("github-actions", "queued")])

    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="racy", token="t"
    )
    assert state == "pending"

    clock.advance(119.0)  # still within grace
    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="racy", token="t"
    )
    assert state == "pending"
    assert ("acme", "widgets", "racy") not in client._zero_checks_first_seen


@pytest.mark.anyio
async def test_check_runs_appearing_clears_zero_checks_bookkeeping():
    """Once real check-runs show up, the zero-checks grace-period entry for
    that ref must be forgotten (it's resolved, not "still ambiguous")."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/check-suites"):
            return _check_suites_response([])
        calls["n"] += 1
        if calls["n"] == 1:
            return _check_runs_response([])
        return _check_runs_response(
            [{"name": "test", "status": "queued", "conclusion": None}]
        )

    clock = _FakeClock()
    client = _client(handler, clock=clock, grace_s=120.0)

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="sha1", token="t"
    )
    assert state == "pending"
    assert ("acme", "widgets", "sha1") in client._zero_checks_first_seen

    state, _ = await client.check_state(
        owner="acme", repo="widgets", ref="sha1", token="t"
    )
    assert (
        state == "pending"
    )  # a real, queued check-run now — still pending, but resolved
    assert ("acme", "widgets", "sha1") not in client._zero_checks_first_seen


# ---- transport-level errors ----------------------------------------------------


@pytest.mark.anyio
async def test_check_suites_http_failure_raises_github_pr_error():
    handler = _routed(
        check_runs=_check_runs_response([]),
        check_suites=httpx.Response(500, text="boom"),
    )
    with pytest.raises(GitHubPrError):
        await _client(handler).check_state(
            owner="acme", repo="widgets", ref="x", token="t"
        )
