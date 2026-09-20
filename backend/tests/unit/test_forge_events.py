"""Signed events reach only the deployment that opened the outbound socket."""

import asyncio
import hashlib
import hmac
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import forge_events_app as relay


@pytest.mark.anyio
async def test_event_and_poll_cannot_advance_the_same_card_twice(
    db_factory, monkeypatch
):
    from types import SimpleNamespace

    from app.domain.project.models import Project
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

    async with db_factory() as session:
        project = Project(name="Concurrent event test")
        session.add(project)
        await session.flush()
        room = Topic(project_id=project.id, title="Review room", created_by="requester")
        session.add(room)
        await session.flush()
        card = AcceptCard(topic_id=room.id, reviewer_handle="reviewer", pr_number=1)
        session.add(card)
        await session.commit()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def poll(*args, **kwargs):
        entered.set()
        await release.wait()

    forge = SimpleNamespace(poll=AsyncMock(side_effect=poll))
    monkeypatch.setattr(AcceptService, "_resolve_forge", AsyncMock(return_value=forge))

    async def advance():
        async with db_factory() as session:
            await AcceptService(session).advance_pr_card(
                card.id, chat_service=None, runner=None
            )
            await session.commit()

    first = asyncio.create_task(advance())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(advance(), 3)
        assert forge.poll.await_count == 1
    finally:
        release.set()
        await first


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        relay.settings,
        "forge_event_relay_keys",
        {"first": "first-secret", "second": "second-secret"},
    )
    with TestClient(relay.app) as test_client:
        yield test_client
    assert not relay.connections


def signed(
    kind="github_app", secret="first-secret", repo="owner/project", installation=None
):
    payload = {"repository": {"full_name": repo}, "private": "discard me"}
    if installation is not None:
        payload["installation"] = {"id": installation}
    body = json.dumps(payload)
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    header, value = (
        ("X-Forgejo-Signature", digest)
        if kind == "forgejo"
        else ("X-Hub-Signature-256", f"sha256={digest}")
    )
    return {"content": body, "headers": {header: value}}


@pytest.mark.parametrize("kind", ["github_app", "forgejo"])
def test_signed_event_is_an_invalidation_not_a_copied_payload(client, kind):
    with client.websocket_connect(
        "/forge/events/first/connect", headers={"Authorization": "Bearer first-secret"}
    ) as socket:
        response = client.post("/forge/events/first", **signed(kind))
        assert response.status_code == 202
        assert socket.receive_json() == {"kind": kind, "repo": "owner/project"}
        assert (
            client.post(
                "/forge/events/first", **signed(kind, secret="second-secret")
            ).status_code
            == 401
        )


def test_tampering_and_missing_signatures_are_rejected(client):
    request = signed()
    request["content"] += " "
    assert client.post("/forge/events/first", **request).status_code == 401
    assert client.post("/forge/events/first", json={}).status_code == 401


def test_offline_deployment_requests_retry_and_can_reconnect(client):
    assert client.post("/forge/events/first", **signed()).status_code == 503
    for _ in range(2):
        with client.websocket_connect(
            "/forge/events/first/connect",
            headers={"Authorization": "Bearer first-secret"},
        ) as socket:
            assert client.post("/forge/events/first", **signed()).status_code == 202
            assert socket.receive_json()["repo"] == "owner/project"


def test_a_deployment_cannot_subscribe_to_anothers_events(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/forge/events/second/connect",
            headers={"Authorization": "Bearer first-secret"},
        ):
            pytest.fail("Cross-deployment subscription was accepted")


def test_rolling_release_keeps_the_existing_connection(client):
    with client.websocket_connect(
        "/forge/events/first/connect", headers={"Authorization": "Bearer first-secret"}
    ) as first:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/forge/events/first/connect",
                headers={"Authorization": "Bearer first-secret"},
            ):
                pytest.fail("Duplicate subscription was accepted")
        assert client.post("/forge/events/first", **signed()).status_code == 202
        assert first.receive_json()["repo"] == "owner/project"


def test_body_size_is_bounded_before_json_parsing(client, monkeypatch):
    monkeypatch.setattr(relay, "MAX_BODY", 8)
    assert client.post("/forge/events/first", **signed()).status_code == 413


@pytest.mark.anyio
async def test_listener_reconciles_on_connect_and_retries_disconnect(monkeypatch):
    from app.domain.review import events

    scheduler = AsyncMock()
    attempts = []

    class Socket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if scheduler.forge_repository_changed.await_count:
                raise asyncio.CancelledError
            return json.dumps({"kind": "forgejo", "repo": "owner/project"})

    class Connect:
        async def __aenter__(self):
            if len(attempts) == 1:
                raise ConnectionError("relay restarting")
            return Socket()

        async def __aexit__(self, *args):
            pass

    def connect(url, **kwargs):
        attempts.append((url, kwargs))
        return Connect()

    monkeypatch.setattr(events, "connect", connect)
    monkeypatch.setattr(events.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        events.settings, "forge_event_relay_url", "wss://relay.invalid/connect"
    )
    monkeypatch.setattr(events.settings, "forge_event_secret", "deployment-secret")
    with pytest.raises(asyncio.CancelledError):
        await events.listen(scheduler)
    assert len(attempts) == 2
    assert attempts[1][1]["additional_headers"] == {
        "Authorization": "Bearer deployment-secret"
    }
    scheduler.open_draft_prs.assert_awaited_once()
    scheduler.poll_open_prs.assert_awaited_once()
    scheduler.forge_repository_changed.assert_awaited_once_with(
        kind="forgejo", repo="owner/project"
    )


@pytest.mark.anyio
async def test_event_refresh_is_limited_to_bound_repository(db_factory, monkeypatch):
    from app.domain.project.models import Project, ProjectForge
    from app.domain.review import pr_publish
    from app.domain.scheduler.service import SchedulerService

    async with db_factory() as session:
        first, second = Project(name="First"), Project(name="Second")
        session.add_all([first, second])
        await session.flush()
        for project, repo in ((first, "owner/first"), (second, "owner/second")):
            session.add(
                ProjectForge(
                    project_id=project.id,
                    kind="forgejo",
                    repo=repo,
                    url=f"https://forge.invalid/{repo}.git",
                    api_url="https://forge.invalid/api/v1",
                    default_branch="main",
                )
            )
        await session.commit()
    scheduler = object.__new__(SchedulerService)
    scheduler._sessions = db_factory
    scheduler.poll_open_prs = AsyncMock()
    sweep = AsyncMock()
    monkeypatch.setattr(pr_publish, "sweep_draft_prs", sweep)
    await scheduler.forge_repository_changed("forgejo", "owner/first")
    sweep.assert_awaited_once_with(db_factory, first.id)
    scheduler.poll_open_prs.assert_awaited_once_with(first.id)
    await scheduler.forge_repository_changed("github_app", "owner/first")
    assert sweep.await_count == 1
    await scheduler.forge_repository_changed("forgejo", "owner/first", str(second.id))
    assert sweep.await_count == 1


def test_project_hook_cannot_subscribe_or_sign_for_another_project(client):
    from app.core.forge_events import project_secret

    project_id = uuid.uuid4()
    key = project_secret("first-secret", project_id)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/forge/events/first/connect", headers={"Authorization": f"Bearer {key}"}
        ):
            pytest.fail("Project hook key became a deployment credential")
    with client.websocket_connect(
        "/forge/events/first/connect", headers={"Authorization": "Bearer first-secret"}
    ) as socket:
        assert (
            client.post(
                f"/forge/events/first/{project_id}", **signed("forgejo", secret=key)
            ).status_code
            == 202
        )
        assert socket.receive_json() == {
            "kind": "forgejo",
            "repo": "owner/project",
            "project_id": str(project_id),
        }
        assert (
            client.post(
                f"/forge/events/first/{uuid.uuid4()}", **signed("forgejo", secret=key)
            ).status_code
            == 401
        )


def test_shared_app_routes_installations_without_cross_deployment_delivery(
    client, monkeypatch
):
    monkeypatch.setattr(relay.settings, "forge_event_github_secret", "app-secret")
    monkeypatch.setattr(
        relay.settings,
        "forge_event_github_installations",
        {"11": ["first"], "22": ["second"]},
    )
    with (
        client.websocket_connect(
            "/forge/events/first/connect",
            headers={"Authorization": "Bearer first-secret"},
        ) as first,
        client.websocket_connect(
            "/forge/events/second/connect",
            headers={"Authorization": "Bearer second-secret"},
        ) as second,
    ):
        assert (
            client.post(
                "/forge/events/github-app",
                **signed(secret="app-secret", installation=11),
            ).status_code
            == 202
        )
        assert first.receive_json() == {"kind": "github_app", "repo": "owner/project"}

        assert (
            client.post(
                "/forge/events/github-app",
                **signed(secret="app-secret", installation=22, repo="other/project"),
            ).status_code
            == 202
        )
        # An incorrectly broadcast first event would be ahead of this one.
        assert second.receive_json() == {"kind": "github_app", "repo": "other/project"}
        assert (
            client.post(
                "/forge/events/first", **signed(repo="owner/marker")
            ).status_code
            == 202
        )
        assert first.receive_json()["repo"] == "owner/marker"


def test_shared_app_delivers_to_online_subscribers_when_another_is_offline(
    client, monkeypatch
):
    monkeypatch.setattr(relay.settings, "forge_event_github_secret", "app-secret")
    monkeypatch.setattr(
        relay.settings, "forge_event_github_installations", {"11": ["first", "second"]}
    )
    with client.websocket_connect(
        "/forge/events/first/connect", headers={"Authorization": "Bearer first-secret"}
    ) as first:
        assert (
            client.post(
                "/forge/events/github-app",
                **signed(secret="app-secret", installation=11),
            ).status_code
            == 503
        )
        assert first.receive_json() == {"kind": "github_app", "repo": "owner/project"}
        with client.websocket_connect(
            "/forge/events/second/connect",
            headers={"Authorization": "Bearer second-secret"},
        ) as second:
            assert (
                client.post(
                    "/forge/events/github-app",
                    **signed(
                        secret="app-secret", installation=11, repo="owner/reconnected"
                    ),
                ).status_code
                == 202
            )
            expected = {"kind": "github_app", "repo": "owner/reconnected"}
            assert first.receive_json() == expected
            assert second.receive_json() == expected


def test_deployment_key_cannot_forge_shared_app_events(client, monkeypatch):
    monkeypatch.setattr(relay.settings, "forge_event_github_secret", "app-secret")
    assert (
        client.post("/forge/events/github-app", **signed(installation=11)).status_code
        == 401
    )
    assert (
        client.post(
            "/forge/events/github-app", **signed(secret="app-secret")
        ).status_code
        == 400
    )
    # Installations not assigned to Cheese deployments do not receive events.
    assert (
        client.post(
            "/forge/events/github-app", **signed(secret="app-secret", installation=99)
        ).status_code
        == 202
    )
