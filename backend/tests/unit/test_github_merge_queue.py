import json

import httpx
import pytest

from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPrError,
    HttpxGitHubPrClient,
)


@pytest.mark.anyio
@pytest.mark.parametrize("already_queued", [False, True])
@pytest.mark.parametrize("app_token", [False, True])
async def test_queue_required_never_directly_merges(already_queued, app_token):
    requests = []

    def handler(request):
        assert request.url.path == "/graphql"
        body = json.loads(request.content)
        requests.append(body)
        if "query(" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_7",
                                "headRefOid": "reviewed",
                                "isMergeQueueEnabled": True,
                                "mergeQueueEntry": {"id": "Q_7"}
                                if already_queued
                                else None,
                            }
                        }
                    }
                },
            )
        assert body["variables"] == {"id": "PR_7", "sha": "reviewed"}
        return httpx.Response(
            200,
            json={
                "data": {
                    "enqueuePullRequest": {
                        "mergeQueueEntry": {"id": "Q_7"},
                    }
                }
            },
        )

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    if app_token:
        from typing import cast

        from app.domain.agent.github_app import GitHubAppTokens
        from tests.unit.test_github_pr import _FakeTokens

        bound = GitHubPRClient(
            "acme",
            "widgets",
            cast(GitHubAppTokens, _FakeTokens()),
            transport=httpx.MockTransport(handler),
        )
        result = await bound.merge_pr(7, title="fix: example", message="")
        assert result == {"merged": False, "queued": True, "sha": None}
    else:
        result = await client.merge_pull_request(
            owner="acme",
            repo="widgets",
            number=7,
            token="t",
            sha="reviewed",
        )
        assert result.queued and result.sha is None and result.blocked_reason is None
    assert len(requests) == (1 if already_queued else 2)


@pytest.mark.anyio
async def test_changed_head_never_enters_queue():
    def handler(request):
        assert "query(" in json.loads(request.content)["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_7",
                            "headRefOid": "unreviewed",
                            "isMergeQueueEnabled": True,
                            "mergeQueueEntry": None,
                        }
                    }
                }
            },
        )

    result = await HttpxGitHubPrClient(
        transport=httpx.MockTransport(handler)
    ).merge_pull_request(
        owner="acme",
        repo="widgets",
        number=7,
        token="t",
        sha="reviewed",
    )
    assert result.stale_head and not result.queued and result.sha is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        {"errors": [{"message": "permission denied"}]},
        {"data": {"repository": {"pullRequest": None}}},
    ],
)
async def test_queue_lookup_errors_never_fall_back_to_merge(response):
    def handler(request):
        assert request.url.path == "/graphql"
        return httpx.Response(200, json=response)

    with pytest.raises(GitHubPrError):
        await HttpxGitHubPrClient(
            transport=httpx.MockTransport(handler)
        ).merge_pull_request(
            owner="acme",
            repo="widgets",
            number=7,
            token="t",
            sha="reviewed",
        )


@pytest.mark.anyio
async def test_enqueue_refusal_is_not_reported_as_queued():
    def handler(request):
        if "mutation(" in json.loads(request.content)["query"]:
            return httpx.Response(
                200, json={"errors": [{"message": "Required check failed"}]}
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_7",
                            "headRefOid": "reviewed",
                            "isMergeQueueEnabled": True,
                            "mergeQueueEntry": None,
                        }
                    }
                }
            },
        )

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubPrError, match="Required check failed"):
        await client.merge_pull_request(
            owner="acme", repo="widgets", number=7, token="t", sha="reviewed"
        )


@pytest.mark.anyio
async def test_dequeue_uses_pull_request_id_and_preserves_api_errors():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if "mutation(" in body["query"]:
            assert body["variables"] == {"id": "PR_7"}
            return httpx.Response(
                200, json={"errors": [{"message": "permission denied"}]}
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_7",
                            "headRefOid": "reviewed",
                            "isMergeQueueEnabled": True,
                            "mergeQueueEntry": {"id": "Q_7"},
                        }
                    }
                }
            },
        )

    client = HttpxGitHubPrClient(transport=httpx.MockTransport(handler))
    assert await client.merge_queue_entry(
        owner="acme", repo="widgets", number=7, token="t"
    )
    with pytest.raises(GitHubPrError, match="permission denied"):
        await client.dequeue_pull_request(
            owner="acme", repo="widgets", number=7, token="t"
        )
