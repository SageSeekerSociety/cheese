"""`HttpxGitHubPrClient.open_pull_request` — opening a PR, and the one 422
that must NOT be treated as a failure.

Background (2026-08-10): accepting topic `0bbc3403` twice raced two PR-open
calls against the same head branch. The loser got GitHub's 422 "a pull request
already exists", the caller read every exception as "PR mechanism unavailable",
degraded to a local merge + direct push to main — and left PR #234 open
forever with CI burning on code that had already landed by another route.

These tests drive the REAL client through a mocked transport (never a
monkeypatched `open_pull_request`), so the status/body discrimination and the
follow-up listing call actually run.
"""

import httpx
import pytest

from app.domain.review.github_pr import GitHubPrError, HttpxGitHubPrClient

_ALREADY_EXISTS_BODY = {
    "message": "Validation Failed",
    "errors": [
        {
            "resource": "PullRequest",
            "code": "custom",
            "message": "A pull request already exists for acme:cheesex/0bbc3403.",
        }
    ],
}


def _pr_payload(number: int, *, head: str, base: str = "main", sha: str) -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/acme/widgets/pull/{number}",
        "head": {"ref": head, "sha": sha},
        "base": {"ref": base},
    }


def _client(handler) -> HttpxGitHubPrClient:
    return HttpxGitHubPrClient(transport=httpx.MockTransport(handler))


async def _open(client: HttpxGitHubPrClient, *, head: str = "cheesex/0bbc3403"):
    return await client.open_pull_request(
        owner="acme",
        repo="widgets",
        head=head,
        base="main",
        title="[cheesex] 做一个东西",
        body="Reviewed-by: alice",
        token="t",
    )


def _routed(*, post: httpx.Response, listing: httpx.Response | None = None):
    """POST /pulls -> `post`; GET /pulls -> `listing` (asserts it isn't called
    when the test didn't provide one)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return post
        if request.method == "GET" and listing is not None:
            return listing
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler, calls


@pytest.mark.anyio
async def test_created_pr_is_returned_and_not_marked_as_existing():
    handler, calls = _routed(
        post=httpx.Response(
            201, json=_pr_payload(300, head="cheesex/0bbc3403", sha="aaa111")
        )
    )
    pr = await _open(_client(handler))

    assert (pr.number, pr.head_sha) == (300, "aaa111")
    assert pr.url.endswith("/pull/300")
    assert pr.already_existed is False
    # Happy path must stay one call — no speculative listing.
    assert [c.method for c in calls] == ["POST"]


@pytest.mark.anyio
async def test_already_exists_422_claims_the_open_pr_on_that_branch():
    """The whole point: 422-already-exists resolves to the existing PR instead
    of raising, so the caller stays on the PR path (no degrade, no orphan)."""
    handler, calls = _routed(
        post=httpx.Response(422, json=_ALREADY_EXISTS_BODY),
        listing=httpx.Response(
            200, json=[_pr_payload(234, head="cheesex/0bbc3403", sha="b8359385")]
        ),
    )
    pr = await _open(_client(handler))

    assert pr.number == 234
    assert pr.url == "https://github.com/acme/widgets/pull/234"
    assert pr.head_sha == "b8359385"
    assert pr.already_existed is True

    listing = calls[1]
    assert listing.method == "GET"
    # GitHub's head filter is `user:ref`, and only OPEN PRs count — a closed
    # one on the same branch is not something to adopt.
    assert listing.url.params["head"] == "acme:cheesex/0bbc3403"
    assert listing.url.params["state"] == "open"


@pytest.mark.anyio
async def test_already_exists_picks_the_pr_against_the_requested_base():
    """One head branch can have open PRs against several bases; the one we
    were trying to open wins over list order."""
    handler, _ = _routed(
        post=httpx.Response(422, json=_ALREADY_EXISTS_BODY),
        listing=httpx.Response(
            200,
            json=[
                _pr_payload(11, head="cheesex/0bbc3403", base="release", sha="ccc"),
                _pr_payload(12, head="cheesex/0bbc3403", base="main", sha="ddd"),
            ],
        ),
    )
    pr = await _open(_client(handler))

    assert (pr.number, pr.head_sha) == (12, "ddd")


@pytest.mark.anyio
async def test_other_422_still_raises_so_the_caller_degrades():
    """A real validation failure (bad base, empty diff, head == base) is NOT
    "the PR is already there" — it must keep raising, or every broken PR-open
    would silently look like a success."""
    handler, calls = _routed(
        post=httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "PullRequest",
                        "field": "base",
                        "code": "invalid",
                        "message": "Base ref must be a branch",
                    }
                ],
            },
        )
    )
    with pytest.raises(GitHubPrError) as excinfo:
        await _open(_client(handler))

    assert "422" in str(excinfo.value)
    assert "Base ref must be a branch" in str(excinfo.value)
    # Never went looking for a PR to claim.
    assert [c.method for c in calls] == ["POST"]


@pytest.mark.anyio
async def test_already_exists_wording_outside_the_pullrequest_resource_still_raises():
    """Matching on the structured error entry, not a substring of the body:
    another resource saying "already exists" is a different problem."""
    handler, calls = _routed(
        post=httpx.Response(
            422,
            json={
                "message": "Validation Failed",
                "errors": [
                    {
                        "resource": "Issue",
                        "code": "custom",
                        "message": "A label already exists",
                    }
                ],
            },
        )
    )
    with pytest.raises(GitHubPrError):
        await _open(_client(handler))
    assert [c.method for c in calls] == ["POST"]


@pytest.mark.anyio
async def test_already_exists_but_listing_finds_nothing_raises_the_original_error():
    """Closed between the two calls / GitHub disagreeing with itself: degrade
    on the error GitHub actually returned rather than invent a PR."""
    handler, _ = _routed(
        post=httpx.Response(422, json=_ALREADY_EXISTS_BODY),
        listing=httpx.Response(200, json=[]),
    )
    with pytest.raises(GitHubPrError) as excinfo:
        await _open(_client(handler))
    assert "422" in str(excinfo.value)


@pytest.mark.anyio
async def test_already_exists_with_a_failing_listing_raises_the_original_error():
    handler, _ = _routed(
        post=httpx.Response(422, json=_ALREADY_EXISTS_BODY),
        listing=httpx.Response(503, text="upstream unavailable"),
    )
    with pytest.raises(GitHubPrError) as excinfo:
        await _open(_client(handler))
    assert "422" in str(excinfo.value)


@pytest.mark.anyio
async def test_non_422_failures_are_unchanged():
    handler, calls = _routed(post=httpx.Response(403, text="Resource not accessible"))
    with pytest.raises(GitHubPrError) as excinfo:
        await _open(_client(handler))
    assert "403" in str(excinfo.value)
    assert [c.method for c in calls] == ["POST"]
