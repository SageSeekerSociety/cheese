"""The GitHub PR client (PR-based accept, #188 §5.1).

Functional: a mock GitHub records what the client sends; the tests assert the
requests (which token, which payload) and the client's reading of the replies.
"""

import json
from typing import cast

import httpx
import pytest

from app.core.config import settings
from app.domain.agent.github_app import GitHubAppTokens
from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPRError,
    GitHubPRMergeBlocked,
    parse_github_repo,
)


class _FakeTokens:
    """Distinguishable write/read tokens so requests reveal which was used."""

    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"

    async def readonly_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"


def _client(handler) -> GitHubPRClient:
    return GitHubPRClient(
        "acme",
        "widgets",
        cast(GitHubAppTokens, _FakeTokens()),
        transport=httpx.MockTransport(handler),
    )


# ---- parse_github_repo -------------------------------------------------------


def test_parse_accepts_https_github_shapes():
    assert parse_github_repo("https://github.com/acme/widgets") == ("acme", "widgets")
    assert parse_github_repo("https://github.com/acme/widgets.git") == (
        "acme",
        "widgets",
    )
    assert parse_github_repo("https://github.com/acme/widgets/") == ("acme", "widgets")


def test_parse_rejects_everything_else():
    # SSH is excluded on purpose: installation tokens only work over https.
    assert parse_github_repo("git@github.com:acme/widgets.git") is None
    assert parse_github_repo("https://gitlab.com/acme/widgets") is None
    assert parse_github_repo("/home/repos/widgets") is None
    assert parse_github_repo(None) is None
    assert parse_github_repo("") is None


# ---- open_pr -----------------------------------------------------------------


@pytest.mark.anyio
async def test_open_pr_creates_with_the_write_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"number": 7, "html_url": "https://pr/7"})

    pr = await _client(handler).open_pr(
        head="topic/abcd1234", base="main", title="做一个东西", body="正文"
    )

    assert pr["number"] == 7
    [request] = seen
    assert request.url.path == "/repos/acme/widgets/pulls"
    assert request.headers["authorization"] == "Bearer ghs_write"
    assert json.loads(request.content) == {
        "title": "做一个东西",
        "head": "topic/abcd1234",
        "base": "main",
        "body": "正文",
    }


@pytest.mark.anyio
async def test_open_pr_finds_the_already_open_pr():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                422, json={"message": "A pull request already exists for x."}
            )
        assert request.url.params["head"] == "acme:topic/abcd1234"
        return httpx.Response(200, json=[{"number": 5, "html_url": "https://pr/5"}])

    pr = await _client(handler).open_pr(
        head="topic/abcd1234", base="main", title="t", body=""
    )
    assert pr["number"] == 5


@pytest.mark.anyio
async def test_open_pr_surfaces_other_refusals():
    handler = lambda request: httpx.Response(403, text="forbidden")  # noqa: E731
    with pytest.raises(GitHubPRError, match="403"):
        await _client(handler).open_pr(head="b", base="main", title="t", body="")


# ---- merge_pr ----------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_pr_sends_the_configured_merge_method():
    """Was pinned to "merge" — which this squash-only repo refuses with 405 on
    every attempt (docs/infrastructure.md §Merge policy)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"merged": True, "sha": "abc"})

    result = await _client(handler).merge_pr(
        7, title="fix: stop the crash (#7)", message="Reviewed-by: alice"
    )

    assert result["merged"] is True
    [request] = seen
    assert request.url.path == "/repos/acme/widgets/pulls/7/merge"
    assert json.loads(request.content) == {
        "merge_method": settings.accept_pr_merge_method,
        "commit_title": "fix: stop the crash (#7)",
        "commit_message": "Reviewed-by: alice",
    }
    assert settings.accept_pr_merge_method == "squash"  # the repo's policy


@pytest.mark.anyio
async def test_merge_refusal_is_a_distinct_conflict_signal():
    handler = lambda request: httpx.Response(  # noqa: E731
        405, json={"message": "Pull Request is not mergeable"}
    )
    with pytest.raises(GitHubPRMergeBlocked):
        await _client(handler).merge_pr(7, title="t", message="")


@pytest.mark.anyio
async def test_merge_other_failure_is_not_a_conflict():
    handler = lambda request: httpx.Response(500, text="boom")  # noqa: E731
    with pytest.raises(GitHubPRError) as excinfo:
        await _client(handler).merge_pr(7, title="t", message="")
    assert not isinstance(excinfo.value, GitHubPRMergeBlocked)


# ---- check_runs --------------------------------------------------------------


@pytest.mark.anyio
async def test_check_runs_use_the_readonly_token_and_simplify():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {
                        "name": "test",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://run/1",
                        "noise": "dropped",
                    }
                ]
            },
        )

    checks = await _client(handler).check_runs("abc123")

    assert checks == [
        {
            "name": "test",
            "status": "completed",
            "conclusion": "success",
            "url": "https://run/1",
        }
    ]
    [request] = seen
    # Display path rides the sandbox-grade read-only mint, not the write one.
    assert request.headers["authorization"] == "Bearer ghs_read"
    assert request.url.path == "/repos/acme/widgets/commits/abc123/check-runs"


# ---- open_pr: whose PR is it (2026-08-16) -----------------------------------
#
# GitHub attributes a PR to whoever's credential created it, so opening every
# PR with the App's token made every PR on the platform belong to the bot —
# no avatar, no "opened by you", no filter-by-author for the person whose work
# it is. An App cannot impersonate a user; their own token is the only way.


def _pull_recorder(*, user_status: int = 201, app_status: int = 201):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        seen.append((request.method, token))
        if request.method != "POST":
            return httpx.Response(200, json=[{"number": 9}])
        status = user_status if token == "ghu_alice" else app_status
        if status == 201:
            return httpx.Response(201, json={"number": 42})
        return httpx.Response(status, json={"message": "already exist"})

    return handler, seen


@pytest.mark.anyio
async def test_open_pr_uses_the_requester_s_own_token():
    handler, seen = _pull_recorder()

    pr = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body="", as_user_token="ghu_alice"
    )

    assert pr["number"] == 42
    assert seen == [("POST", "ghu_alice")]  # the App token never gets a turn


@pytest.mark.anyio
async def test_open_pr_falls_back_to_the_app_when_the_user_cannot():
    """Their authorization may be revoked, or they may have left the org. A PR
    under the wrong name beats no PR at all — the accept depends on it."""
    handler, seen = _pull_recorder(user_status=403)

    pr = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body="", as_user_token="ghu_alice"
    )

    assert pr["number"] == 42
    assert seen == [("POST", "ghu_alice"), ("POST", "ghs_write")]


@pytest.mark.anyio
async def test_open_pr_adopts_an_existing_pr_without_a_second_create():
    """Re-filing a card for the same topic hits "a pull request already exists"
    — the existing PR IS this topic's PR. Retrying the create with the App
    token would just earn the same 422."""
    handler, seen = _pull_recorder(user_status=422)

    pr = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body="", as_user_token="ghu_alice"
    )

    assert pr["number"] == 9
    assert [method for method, _ in seen] == ["POST", "GET"]


@pytest.mark.anyio
async def test_open_pr_still_works_with_no_user_token_at_all():
    handler, seen = _pull_recorder()

    pr = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body=""
    )

    assert pr["number"] == 42
    assert seen == [("POST", "ghs_write")]
