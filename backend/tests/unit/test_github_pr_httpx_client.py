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

import json

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


# ---- merge_pull_request ---------------------------------------------------------
# 两阶段采纳 shipped having never once merged automatically: the body hardcoded
# `merge_method: "merge"` against a squash-only repo, so GitHub answered 405 on
# every single poll — and the 405 was swallowed into a bare `None` that wrote
# nothing anywhere. These drive the real client through a mocked transport so
# both the request shape and the refusal handling are covered for real.


def _merge_route(response: httpx.Response, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/merge"):
            if seen is not None:
                seen.append(request)
            return response
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


async def _merge(handler, **kwargs):
    return await _client(handler).merge_pull_request(
        owner="acme", repo="widgets", number=7, token="t", **kwargs
    )


@pytest.mark.anyio
async def test_merge_success_returns_sha_and_squashes_by_default():
    seen: list[httpx.Request] = []
    result = await _merge(
        _merge_route(httpx.Response(200, json={"sha": "abc123", "merged": True}), seen),
        commit_title="采纳 修个东西 (#7)",
        commit_message="Reviewed-by: alice",
    )

    assert result.sha == "abc123"
    assert result.blocked_reason is None
    body = json.loads(seen[0].content)
    # The repo is squash-only (docs/infrastructure.md §Merge policy).
    assert body["merge_method"] == "squash"
    # Title and body are separate fields under squash — sending only
    # commit_message would leave the title to GitHub's default.
    assert body["commit_title"] == "采纳 修个东西 (#7)"
    assert body["commit_message"] == "Reviewed-by: alice"


@pytest.mark.anyio
async def test_merge_method_follows_settings():
    seen: list[httpx.Request] = []
    client = HttpxGitHubPrClient(
        transport=httpx.MockTransport(
            _merge_route(httpx.Response(200, json={"sha": "abc"}), seen)
        ),
        merge_method="merge",
    )

    await client.merge_pull_request(owner="acme", repo="widgets", number=7, token="t")

    assert json.loads(seen[0].content)["merge_method"] == "merge"


@pytest.mark.anyio
async def test_merge_405_reports_githubs_own_explanation_instead_of_silence():
    result = await _merge(
        _merge_route(
            httpx.Response(
                405,
                json={"message": "Merge commits are not allowed on this repository"},
            )
        )
    )

    assert result.sha is None
    # Both halves matter: the status code (405 = refused, not "still running")
    # and GitHub's reason (which is the only thing that names the real cause).
    assert "405" in result.blocked_reason
    assert "Merge commits are not allowed" in result.blocked_reason


@pytest.mark.anyio
async def test_merge_409_reports_reason_too():
    result = await _merge(
        _merge_route(httpx.Response(409, json={"message": "Head branch was modified"}))
    )

    assert result.sha is None
    assert "409" in result.blocked_reason
    assert "Head branch was modified" in result.blocked_reason


@pytest.mark.anyio
async def test_merge_refusal_without_json_body_still_reports_something():
    result = await _merge(_merge_route(httpx.Response(405, text="nope")))

    assert result.sha is None
    assert "405" in result.blocked_reason


@pytest.mark.anyio
async def test_merge_hard_failure_still_raises():
    with pytest.raises(GitHubPrError):
        await _merge(_merge_route(httpx.Response(500, text="boom")))


# --- pull_request_status: 谁合的、合到哪个 commit (2026-08-10) ---------------


def _pr_route(payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/acme/widgets/pulls/7"
        return httpx.Response(200, json=payload)

    return handler


async def _status(payload: dict):
    return await _client(_pr_route(payload)).pull_request_status(
        owner="acme", repo="widgets", number=7, token="t"
    )


@pytest.mark.anyio
async def test_status_of_a_merged_pr_reports_the_merge_commit():
    """Shape taken from a real api.github.com response (astral-sh/ruff#20000,
    squash-merged): `merged: true`, `merge_commit_sha` is the commit that
    landed on the base branch, and it is NOT the PR's head."""
    status = await _status(
        {
            "state": "closed",
            "merged": True,
            "merged_at": "2025-08-20T13:19:18Z",
            "merge_commit_sha": "f4be05a83be770c8c9781088508275170396b411",
            "head": {"sha": "6c0badc4333edf4732b3c04f6a583c8dbcaf515c"},
        }
    )

    assert status.merged is True
    assert status.state == "closed"
    assert status.merge_commit_sha == "f4be05a83be770c8c9781088508275170396b411"
    assert status.head_sha == "6c0badc4333edf4732b3c04f6a583c8dbcaf515c"
    assert status.merged_at is not None
    # 项目约定: aware UTC, never naive.
    assert status.merged_at.tzinfo is not None
    assert status.merged_at.isoformat() == "2025-08-20T13:19:18+00:00"


@pytest.mark.anyio
async def test_status_of_an_open_pr_never_reports_its_test_merge_commit():
    """The trap this whole field exists to avoid: GitHub fills
    `merge_commit_sha` on OPEN PRs too, with a throwaway test-merge commit
    that lives on no branch (verified against api.github.com 2026-08-10 with
    astral-sh/ruff#27626 — `compare <that sha>...main` puts it 2 commits
    ahead of main, i.e. not on main at all). Handing that sha to the deploy
    gate would make the card wait forever for a run that cannot exist."""
    status = await _status(
        {
            "state": "open",
            "merged": False,
            "merged_at": None,
            "merge_commit_sha": "cb623502067ee859eca9e9ae779ba95e2c61b26d",
            "head": {"sha": "8e7631069025aa4e4c8fb3c9cf3f55853256a9f2"},
        }
    )

    assert status.merged is False
    assert status.state == "open"
    assert status.merge_commit_sha is None
    assert status.merged_at is None
    assert status.head_sha == "8e7631069025aa4e4c8fb3c9cf3f55853256a9f2"


@pytest.mark.anyio
async def test_status_of_a_closed_unmerged_pr_is_distinguishable_from_merged():
    status = await _status(
        {
            "state": "closed",
            "merged": False,
            "merged_at": None,
            "merge_commit_sha": None,
            "head": {"sha": "deadbeef"},
        }
    )

    assert status.state == "closed"
    assert status.merged is False


@pytest.mark.anyio
async def test_status_merged_with_unparseable_time_still_reports_the_merge():
    """A timestamp surprise must not cost us the merge itself — the caller
    falls back to "now" rather than leaving the card stuck."""
    status = await _status(
        {
            "state": "closed",
            "merged": True,
            "merged_at": "not-a-time",
            "merge_commit_sha": "abc123",
            "head": {"sha": "deadbeef"},
        }
    )

    assert status.merged is True
    assert status.merge_commit_sha == "abc123"
    assert status.merged_at is None


@pytest.mark.anyio
async def test_status_http_failure_raises_github_pr_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with pytest.raises(GitHubPrError):
        await _client(handler).pull_request_status(
            owner="acme", repo="widgets", number=7, token="t"
        )
