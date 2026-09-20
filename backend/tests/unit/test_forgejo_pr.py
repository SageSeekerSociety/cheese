"""Proposal lifecycle against a recording Forgejo HTTP transport."""

import json

import httpx
import pytest

from app.domain.review.forgejo_pr import ForgejoClient, ForgejoPRClient


class Tokens:
    async def write_token(self):
        return "project-token", "2099-01-01T00:00:00Z"

    installation_token = write_token


def proposal(**overrides):
    return {
        "number": 7,
        "title": "WIP: Report",
        "body": "Draft",
        "head": {"ref": "task/report", "sha": "reviewed"},
        "base": {"ref": "main", "sha": "base"},
        "state": "open",
        "merged": False,
        "mergeable": True,
        "html_url": "https://forge.example/acme/docs/pulls/7",
        **overrides,
    }


@pytest.mark.anyio
async def test_draft_is_adopted_then_filed_and_merged_at_reviewed_commit():
    pr = proposal()
    merges = []

    def server(request):
        assert request.headers["Authorization"] == "token project-token"
        path = request.url.path
        if path.endswith("/pulls"):
            assert request.method == "GET"
            return httpx.Response(200, json=[pr])
        if path.endswith("/merge"):
            payload = json.loads(request.content)
            merges.append(payload)
            assert payload["head_commit_id"] == "reviewed"
            assert payload["force_merge"] is False
            assert payload["delete_branch_after_merge"] is False
            pr.update(merged=True, state="closed", merge_commit_sha="landed")
            return httpx.Response(200)
        if request.method == "PATCH":
            pr.update(json.loads(request.content))
        return httpx.Response(200, json=pr)

    transport = httpx.MockTransport(server)
    publisher = ForgejoPRClient(
        "acme",
        "docs",
        Tokens(),
        api_base="https://forge.example/api/v1",
        transport=transport,
    )
    adopted = await publisher.open_pr(
        head="task/report", base="main", title="Report", body="Ready"
    )
    assert adopted.identity_downgrade is None
    adopted = adopted.pr
    assert adopted["number"] == 7
    assert adopted["draft"] is True
    await publisher.mark_ready_for_review(adopted["node_id"])
    assert pr["title"] == "Report"
    client = publisher.client
    status = await client.pull_request_status(
        owner="acme", repo="docs", number=7, token="project-token"
    )
    assert not status.draft
    result = await client.merge_pull_request(
        owner="acme", repo="docs", number=7, token="project-token", sha="reviewed"
    )
    assert result.sha == "landed"
    assert len(merges) == 1


@pytest.mark.anyio
async def test_push_between_review_and_merge_requires_another_review():
    def server(request):
        if request.method == "POST":
            assert json.loads(request.content)["head_commit_id"] == "reviewed"
            return httpx.Response(409, json={"message": "head changed"})
        return httpx.Response(
            200, json=proposal(head={"sha": "new", "ref": "task/report"})
        )

    client = ForgejoClient(
        "https://forge.example/api/v1", transport=httpx.MockTransport(server)
    )
    result = await client.merge_pull_request(
        owner="acme", repo="docs", number=7, token="secret", sha="reviewed"
    )
    assert result.stale_head
    assert result.sha is None


@pytest.mark.anyio
async def test_checks_use_latest_result_per_context_and_read_all_pages():
    def server(request):
        page = request.url.params["page"]
        if page == "1":
            return httpx.Response(
                200,
                json=[
                    {"id": 3, "context": "tests", "status": "success"},
                    {"id": 2, "context": "tests", "status": "failure"},
                ],
                headers={"Link": '<https://forge.example/statuses?page=2>; rel="next"'},
            )
        return httpx.Response(
            200, json=[{"id": 1, "context": "lint", "status": "pending"}]
        )

    client = ForgejoClient(
        "https://forge.example/api/v1", transport=httpx.MockTransport(server)
    )
    state, _ = await client.check_state(
        owner="acme", repo="docs", ref="reviewed", token="secret"
    )
    assert state == "pending"


@pytest.mark.anyio
async def test_merge_refusal_does_not_echo_secret_or_claim_success():
    def server(request):
        if request.method == "POST":
            return httpx.Response(405, json={"message": "request had token secret"})
        return httpx.Response(200, json=proposal(title="Report", mergeable=False))

    client = ForgejoClient(
        "https://forge.example/api/v1", transport=httpx.MockTransport(server)
    )
    result = await client.merge_pull_request(
        owner="acme", repo="docs", number=7, token="secret", sha="reviewed"
    )
    assert result.sha is None
    assert not result.stale_head
    assert "secret" not in result.blocked_reason
    assert "冲突" in result.blocked_reason


def test_unmerged_test_commit_is_never_reported_as_a_delivery():
    status = ForgejoClient.status(proposal(merge_commit_sha="temporary"))
    assert status.merge_commit_sha is None
