"""Count the unlocked preflight's real HTTP boundary, not calls to the sweep."""

import json
import time
import uuid

import httpx
import pytest

from app.core import forge_quota
from app.core.db import release_read_session
from app.core.forge_http import forge_client
from app.domain.agent.announce import announce
from app.domain.delivery.agent import record_task_instruction
from app.domain.review import github_pr, pr_publish
from app.domain.review.github_pr import httpx_client
from app.domain.room_task.models import Task
from tests.integration.test_accept_pr import _FakeTokens, _make_project, _make_topic
from tests.integration.test_accept_pr import app_world as _app_world_fixture

app_world = pytest.fixture(_app_world_fixture.__wrapped__)


def seed_candidates(client, count):
    project = _make_project(client)
    room = _make_topic(client, project)

    async def seed():
        async with client.test_request_factory() as session:
            tasks = [
                Task(
                    project_id=uuid.UUID(project),
                    room_id=uuid.UUID(room),
                    title=f"Unpublished branch {index}",
                    branch_name=f"task/quiet-{index}",
                    base_branch="main",
                )
                for index in range(count)
            ]
            session.add_all(tasks)
            await session.commit()
            return [task.id for task in tasks]

    return client.portal.call(seed)


@pytest.mark.parametrize(
    "missing", [True, False], ids=["missing-heads", "unchanged-heads"]
)
def test_fifty_one_quiet_candidates_replace_individual_rest_with_two_queries(
    client, app_world, monkeypatch, missing
):
    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    ids = seed_candidates(client, 51)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST" and request.url.path == "/graphql":
            variables = json.loads(request.content)["variables"]
            aliases = {}
            for key, branch in variables.items():
                if key.startswith("branch"):
                    aliases[f"r{key[6:]}"] = (
                        None
                        if missing
                        else {
                            "compare": {
                                "baseTarget": {"oid": "head-" + branch},
                                "behindBy": 0,
                            }
                        }
                    )
            return httpx.Response(200, json={"data": {"repository": aliases}})
        if "/branches/" in request.url.path:
            return httpx.Response(
                404 if missing else 200, json={"commit": {"sha": "quiet-head"}}
            )
        if "/compare/" in request.url.path:
            return httpx.Response(200, json={"status": "behind"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    reader = github_pr.HttpxGitHubPrClient(transport=transport)
    monkeypatch.setattr(httpx_client, "_default_client", reader)
    monkeypatch.setattr(forge_quota, "_owners", {})
    monkeypatch.setattr(forge_quota, "_readings", {})
    monkeypatch.setattr(forge_quota, "_refused_until", {})
    forge_quota.own("ghs_app_read", 1, time.time() + 3600)

    async def remote_branch(project_id, session, branch, **kwargs):
        # This fake is the branch-read HTTP boundary. Both the retained old
        # preflight and the new sweep see exactly the same remote facts.
        await release_read_session(session)
        async with forge_client(transport=transport) as http:
            response = await http.get(
                "https://api.github.com/repos/owner/repo/branches/" + branch,
                headers={"Authorization": "Bearer ghs_app_read"},
            )
        return None if response.status_code == 404 else response.json()["commit"]["sha"]

    monkeypatch.setattr(pr_publish, "branch_head", remote_branch)

    async def old_preflight():
        for task_id in ids:
            async with client.test_request_factory() as session:
                assert not await pr_publish._branch_ahead(session, task_id)

    client.portal.call(old_preflight)
    assert len(requests) == (51 if missing else 102)
    assert all(request.method == "GET" for request in requests)
    requests.clear()

    counts = client.portal.call(
        lambda: pr_publish.sweep_draft_prs(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )
    assert counts["opened"] == 0 and counts["failed"] == 0, counts
    assert len(requests) == 2
    assert all(
        request.method == "POST" and request.url.path == "/graphql"
        for request in requests
    )
    assert app_world["opened"] == []


def run_sweep(client):
    return client.portal.call(
        lambda: pr_publish.sweep_draft_prs(
            client.test_request_factory,
            announce=announce,
            record_instruction=record_task_instruction,
        )
    )


def test_no_branch_candidates_do_not_resolve_credentials_or_send_http(
    client, app_world, monkeypatch
):
    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    seed_candidates(client, 0)
    before = _FakeTokens.minted_read
    sent = []

    def handler(request):
        sent.append(request)
        raise AssertionError("no consumer needs this request")

    monkeypatch.setattr(
        httpx_client,
        "_default_client",
        github_pr.HttpxGitHubPrClient(transport=httpx.MockTransport(handler)),
    )
    counts = run_sweep(client)
    assert counts["opened"] == 0 and counts["failed"] == 0
    assert sent == [] and _FakeTokens.minted_read == before


def test_a_bad_pair_is_failed_without_failing_its_valid_sibling(
    client, app_world, monkeypatch
):
    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    seed_candidates(client, 2)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "type": "NOT_FOUND",
                        "path": ["repository", "r0", "compare"],
                        "message": "base unavailable",
                    }
                ],
                "data": {
                    "repository": {
                        "r0": {"compare": None},
                        "r1": {
                            "compare": {
                                "behindBy": 0,
                                "baseTarget": {"oid": "unchanged"},
                            }
                        },
                    }
                },
            },
        )

    monkeypatch.setattr(
        httpx_client,
        "_default_client",
        github_pr.HttpxGitHubPrClient(transport=httpx.MockTransport(handler)),
    )
    counts = run_sweep(client)
    assert counts["failed"] == 1 and counts["opened"] == 0, counts
    assert counts["skipped"] >= 1 and len(sent) == 1


def test_a_broken_comparison_request_keeps_the_no_base_path_independent(
    client, app_world, monkeypatch
):
    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    ids = seed_candidates(client, 2)

    async def no_base():
        async with client.test_request_factory() as session:
            task = await session.get(Task, ids[0])
            assert task is not None
            task.base_branch = None
            await session.commit()

    client.portal.call(no_base)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"data": {"repository": None}})

    monkeypatch.setattr(
        httpx_client,
        "_default_client",
        github_pr.HttpxGitHubPrClient(transport=httpx.MockTransport(handler)),
    )
    counts = run_sweep(client)
    assert counts["failed"] == 1 and counts["opened"] == 0, counts
    assert counts["skipped"] >= 1 and len(sent) == 1


def test_forgejo_keeps_the_existing_per_task_preflight(client, app_world, monkeypatch):
    from types import SimpleNamespace

    from app.domain.project import forge

    monkeypatch.setattr(pr_publish, "enabled", lambda: True)
    ids = seed_candidates(client, 2)
    asked = []

    async def binding(project_id, session):
        return SimpleNamespace(kind="forgejo", repo="owner/repo")

    async def existing_preflight(session, task_id):
        asked.append(task_id)
        return False

    monkeypatch.setattr(forge, "binding_for_project", binding)
    monkeypatch.setattr(pr_publish, "_branch_ahead", existing_preflight)
    counts = run_sweep(client)
    assert counts["opened"] == 0 and counts["failed"] == 0, counts
    assert set(ids).issubset(asked)
