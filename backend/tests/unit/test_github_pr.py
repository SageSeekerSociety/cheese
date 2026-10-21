"""The GitHub PR client (PR-based accept, #188 §5.1).

Functional: a mock GitHub records what the client sends; the tests assert the
requests (which token, which payload) and the client's reading of the replies.
"""

import json
from typing import cast

import httpx
import pytest

from app.core.config import settings
from app.domain.project.github_app import GitHubAppTokens
from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPRError,
    GitHubPRMergeBlocked,
    GitHubPRRateLimited,
)


class _FakeTokens:
    """Distinguishable write/read tokens so requests reveal which was used."""

    async def write_token(self) -> tuple[str, str]:
        return "ghs_write", "2099-01-01T00:00:00+00:00"

    async def installation_token(self) -> tuple[str, str]:
        return "ghs_read", "2099-01-01T00:00:00+00:00"


def _client(handler) -> GitHubPRClient:
    def route(request):
        if (
            request.url.path == "/graphql"
            and "isMergeQueueEnabled" in request.content.decode()
        ):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_7",
                                "headRefOid": "abc",
                                "isMergeQueueEnabled": False,
                                "mergeQueueEntry": None,
                            }
                        }
                    }
                },
            )
        return handler(request)

    return GitHubPRClient(
        "acme",
        "widgets",
        cast(GitHubAppTokens, _FakeTokens()),
        transport=httpx.MockTransport(route),
    )


# ---- open_pr -----------------------------------------------------------------


@pytest.mark.anyio
async def test_open_pr_creates_with_the_write_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"number": 7, "html_url": "https://pr/7"})

    opened = await _client(handler).open_pr(
        head="topic/abcd1234", base="main", title="做一个东西", body="正文"
    )

    assert opened.pr["number"] == 7
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

    opened = await _client(handler).open_pr(
        head="topic/abcd1234", base="main", title="t", body=""
    )
    assert opened.pr["number"] == 5


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
async def test_check_runs_use_the_installation_token_and_simplify():
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


def _pull_recorder(*, app_status: int = 201):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        seen.append((request.method, token))
        if request.method != "POST":
            return httpx.Response(200, json=[{"number": 9}])
        if app_status == 201:
            return httpx.Response(201, json={"number": 42})
        return httpx.Response(app_status, json={"message": "already exist"})

    return handler, seen


@pytest.mark.anyio
async def test_open_pr_adopts_an_existing_pr_without_a_second_create():
    """Re-filing adopts the existing PR for the task branch."""
    handler, seen = _pull_recorder(app_status=422)

    opened = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body=""
    )

    assert opened.pr["number"] == 9
    assert [method for method, _ in seen] == ["POST", "GET"]


@pytest.mark.anyio
async def test_open_pr_uses_the_app_identity():
    handler, seen = _pull_recorder()

    opened = await _client(handler).open_pr(
        head="topic/1", base="main", title="fix: x", body=""
    )

    assert opened.pr["number"] == 42
    assert seen == [("POST", "ghs_write")]


# ---- commit_subject ----------------------------------------------------------


@pytest.mark.anyio
async def test_commit_subject_is_the_messages_first_line():
    """What goes on a draft PR before it is marked ready: a commit's message is
    its subject and then its body, and only the subject belongs in a title."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "commit": {
                    "message": "fix(usage): a claim is answered once\n\nWhy it was not."
                }
            },
        )

    subject = await _client(handler).commit_subject("abc123")

    assert subject == "fix(usage): a claim is answered once"
    [request] = seen
    assert request.url.path == "/repos/acme/widgets/commits/abc123"


@pytest.mark.anyio
async def test_commit_subject_refusals_raise_what_callers_catch():
    """`GitHubPrError` (the older client's) and `GitHubPRError` are unrelated:
    the ready path catches the pair below and nothing else, so a refusal here
    in the other class would reach `mark_ready`'s caller as a 500 — including
    the rate limit `pr_view` two methods up turns into an answer."""
    rate_limited = lambda: _client(  # noqa: E731 — a transport, twice
        lambda request: httpx.Response(
            403, text="API rate limit exceeded for installation"
        )
    ).commit_subject("abc123")

    with pytest.raises(GitHubPRRateLimited) as limited:
        await rate_limited()
    assert limited.value.args[0].key == "githubRefusedCommitRead"

    with pytest.raises(GitHubPRError) as refused:
        await _client(lambda request: httpx.Response(500, text="boom")).commit_subject(
            "abc123"
        )
    assert type(refused.value) is GitHubPRError
    assert refused.value.args[0].key == "githubRefusedCommitRead"


# ---- commit_parents ----------------------------------------------------------


@pytest.mark.anyio
async def test_commit_parents_are_the_parent_shas_in_order():
    """A line that merged the base branch in ends on a two-parent commit; the
    ready title has to walk the FIRST parent, and the order is what says which
    one that is. The second parent is the base branch's history."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"parents": [{"sha": "task-tip"}, {"sha": "main-tip"}]}
        )

    parents = await _client(handler).commit_parents("abc123")

    assert parents == ["task-tip", "main-tip"]
    [request] = seen
    assert request.url.path == "/repos/acme/widgets/commits/abc123"


@pytest.mark.anyio
async def test_commit_parents_of_a_root_commit_are_empty():
    """A root commit has no parents, and a parent the forge left without a sha
    is not one either: neither may crash the walk or produce a None sha the
    next read would be addressed to."""
    root = await _client(
        lambda _request: httpx.Response(200, json={"commit": {"message": "first"}})
    ).commit_parents("abc123")
    shapeless = await _client(
        lambda _request: httpx.Response(200, json={"parents": [{"sha": None}, {}]})
    ).commit_parents("abc123")

    assert root == []
    assert shapeless == []


@pytest.mark.anyio
async def test_commit_parents_refusals_raise_what_callers_catch():
    """The same two refusals `commit_subject` raises: the ready path catches
    this pair and nothing else, so the quota has to reach it as the answer it
    already knows how to give."""
    with pytest.raises(GitHubPRRateLimited) as limited:
        await _client(
            lambda _request: httpx.Response(
                403, text="API rate limit exceeded for installation"
            )
        ).commit_parents("abc123")
    assert limited.value.args[0].key == "githubRefusedCommitRead"

    with pytest.raises(GitHubPRError) as refused:
        await _client(lambda _request: httpx.Response(500, text="boom")).commit_parents(
            "abc123"
        )
    assert type(refused.value) is GitHubPRError


# ---- unreachable GitHub ------------------------------------------------------


@pytest.mark.anyio
async def test_pr_view_distinguishes_installation_rate_limit_from_permission_403():
    limited = _client(
        lambda _request: httpx.Response(
            403,
            json={"message": "API rate limit exceeded for installation ID 152342238."},
        )
    )
    with pytest.raises(GitHubPRRateLimited):
        await limited.pr_view(7)

    forbidden = _client(
        lambda _request: httpx.Response(
            403, json={"message": "Resource not accessible"}
        )
    )
    with pytest.raises(GitHubPRError) as error:
        await forbidden.pr_view(7)
    assert type(error.value) is GitHubPRError


@pytest.mark.anyio
@pytest.mark.parametrize("spend_at", ["update_pr", "mark_ready_for_review"])
@pytest.mark.parametrize("refusal", ["quota", "permission"])
async def test_ready_writes_read_a_403_the_way_the_reads_do(spend_at, refusal):
    """The ready hand-over is three calls against one installation quota, and
    any of them can be the one that spends the last request.

    `mark_ready` answers `GitHubPRRateLimited` with a retry sentence and lets
    the parent class through as a 500, so a write that reported the quota as an
    ordinary refusal would be wrong for exactly the case `pr_view` two calls up
    was changed to handle. The two 403s are the same status and differ only by
    the phrase, so both directions are pinned here."""

    def transport(_request: httpx.Request) -> httpx.Response:
        if refusal == "quota":
            return httpx.Response(403, text="API rate limit exceeded for installation")
        return httpx.Response(403, json={"message": "Resource not accessible"})

    expected = GitHubPRRateLimited if refusal == "quota" else GitHubPRError
    call = (
        (lambda c: c.update_pr(7, title="fix(x): y"))
        if spend_at == "update_pr"
        else (lambda c: c.mark_ready_for_review("PR_node"))
    )
    with pytest.raises(expected) as error:
        await call(_client(transport))
    assert type(error.value) is expected


def _unreachable(request: httpx.Request) -> httpx.Response:
    """api.github.com resolved but the TLS handshake never completed — the
    real 2026-08-17 failure, verbatim from the traceback."""
    raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.pr_view(7),
        lambda c: c.commit_subject("abc123"),
        lambda c: c.commit_parents("abc123"),
        lambda c: c.check_runs("abc123"),
        lambda c: c.merge_pr(7, title="t", message="m"),
        lambda c: c.open_pr(head="topic/1", base="main", title="t", body=""),
    ],
)
async def test_network_failure_surfaces_as_a_github_pr_error(call):
    """Not reaching GitHub is the same thing to every caller as GitHub saying
    no — both mean "I could not learn the PR's state". Leaking httpx's own
    exception instead skipped every `except GitHubPRError` in the codebase and
    became a 500 out of /pr-checks, which polls on a timer."""
    with pytest.raises(GitHubPRError) as excinfo:
        await call(_client(_unreachable))

    assert "unreachable" in str(excinfo.value)
    assert "ConnectError" in str(excinfo.value)
