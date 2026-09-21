"""The PR checks panel reads the project's authoritative forge.

Missing-binding and legacy-card delivery refusals are covered by test_accept_pr.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers


def test_unreadable_binding_keeps_cards_visible_and_refuses_accept(client, monkeypatch):
    from app.domain.project import forge

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    monkeypatch.setattr(
        forge, "binding_for_project", AsyncMock(side_effect=OSError("unavailable"))
    )
    listed = client.get(f"/topics/{tid}/accept-card")
    assert listed.status_code == 200
    card = listed.json()["data"]["data"][0]
    assert card["forge"]["kind"] == "unknown"
    assert card["merge_state"]["state"] == "unknown"
    refused = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert refused.status_code == 422
    assert "无法读取" in refused.json()["message"]


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _give_card_a_pr(client, card_id: str, number: int = 7) -> None:
    """Seed the card as pr_publish would have: it rides PR #<number>."""

    async def _do() -> None:
        from app.domain.review.repositories import AcceptCardRepository

        async with client.test_factory() as session:
            card = await AcceptCardRepository(session).get(uuid.UUID(card_id))
            assert card is not None
            card.pr_number = number
            card.pr_url = f"https://github.com/acme/widgets/pull/{number}"
            await session.commit()

    asyncio.run(_do())


@pytest.mark.parametrize("kind", ["github_app", "forgejo"])
def test_pr_checks_endpoint_mirrors_forge_check_runs(client, monkeypatch, kind):
    from app.domain.project import forge

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid)

    async def select_provider():
        async with client.test_factory() as session:
            binding = await forge.binding_for_project(uuid.UUID(pid), session)
            binding.kind = kind
            await session.commit()

    asyncio.run(select_provider())
    checks = [
        {
            "name": "test",
            "status": "completed",
            "conclusion": "success",
            "url": "https://forge.test/check/1",
        }
    ]
    selected = []

    class Provider:
        def __init__(self, owner, repo, tokens, **kwargs):
            selected.append(kind)

        async def pr_view(self, number):
            assert number == 7
            return {
                "merged": False,
                "state": "open",
                "mergeable": True,
                "head": {"sha": "abc123"},
            }

        async def check_runs(self, ref):
            assert ref == "abc123"
            return checks

    def wrong_provider(*args, **kwargs):
        raise AssertionError("Used a different forge than the project binding")

    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        forge, "GitHubPRClient", Provider if kind == "github_app" else wrong_provider
    )
    monkeypatch.setattr(
        forge, "ForgejoPRClient", Provider if kind == "forgejo" else wrong_provider
    )
    response = client.get(f"/topics/{tid}/pr-checks")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["pr_number"] == 7 and data["state"] == "open"
    assert data["mergeable"] is True and data["checks"] == checks
    assert selected == [kind]


@pytest.mark.parametrize("stage", ["credentials", "unconfigured", "view", "checks"])
def test_pr_checks_report_unavailable_without_raising(client, monkeypatch, stage):
    from app.api.routes import accept
    from app.core.errors import GatewayUnavailableError

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    _give_card_a_pr(client, cid)
    provider = AsyncMock()
    provider.pr_view.return_value = {"head": {"sha": "abc123"}}
    provider.check_runs.return_value = []
    factory = AsyncMock(return_value=provider)
    failure = httpx.ConnectError("Forge unavailable")
    if stage == "unconfigured":
        factory.side_effect = GatewayUnavailableError("项目的代码托管凭据尚未配置")
    elif stage == "credentials":
        factory.side_effect = failure
    elif stage == "view":
        provider.pr_view.side_effect = failure
    else:
        provider.check_runs.side_effect = failure
    monkeypatch.setattr(accept, "proposal_client", factory)
    response = client.get(f"/topics/{tid}/pr-checks")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is False
    assert ("尚未配置" if stage == "unconfigured" else "ConnectError") in data["reason"]


def test_prless_card_does_not_request_forge_checks(client, monkeypatch):
    from app.api.routes import accept

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid)
    factory = AsyncMock(side_effect=AssertionError("No PR to read"))
    monkeypatch.setattr(accept, "proposal_client", factory)
    response = client.get(f"/topics/{tid}/pr-checks")
    assert response.status_code == 200
    assert response.json()["data"] == {"available": False}
    factory.assert_not_awaited()
