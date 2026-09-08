"""Failed setup is handed to overview once and repair is scoped to that room."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.project.environment_recovery import latest_recovery, report_failure
from app.domain.project.models import Project
from tests.integration.test_project_environment import project, room


def setup_incident(client, monkeypatch):
    project_id, owner = project(client)
    topic_id = room(client, project_id)
    chat = client.app.dependency_overrides[get_chat_service]()
    runner = SimpleNamespace(
        submit=Mock(),
        submit_kickoff=Mock(return_value=uuid.uuid4()),
        kickoff_pending=lambda turn_id: True,
    )
    monkeypatch.setattr("app.api.deps.get_work_runner", lambda: runner)

    async def initialize():
        async with chat.session_factory() as db:
            p = await db.get(Project, uuid.UUID(project_id))
            root_id = str(p.root_topic_id)
        state = {
            "state": "failed",
            "attempt": "first",
            "stage": "setup",
            "exit_code": 1,
        }
        await report_failure(chat, uuid.UUID(project_id), uuid.UUID(topic_id), state)
        await report_failure(chat, uuid.UUID(project_id), uuid.UUID(topic_id), state)
        async with chat.session_factory() as db:
            incident = await latest_recovery(db, uuid.UUID(topic_id))
            assert incident.meta["state"] == "requested"
            return root_id, str(incident.id)

    root_id, incident_id = client.portal.call(initialize)
    assert runner.submit_kickoff.call_count == 1
    return project_id, topic_id, root_id, incident_id, owner, chat, runner


def test_only_overview_can_inspect_and_repair_once(client, monkeypatch):
    from app.api.routes import project_environment as routes

    p, t, root, incident, owner, chat, runner = setup_incident(client, monkeypatch)
    path = f"/projects/{p}/environment/recovery/rooms/{t}"
    headers = {"X-Cheese-Token": mint_scoped_token(project_id=p, topic_id=root)}
    assert client.get(path).status_code == 403
    assert client.get(path, headers=owner).status_code == 403
    assert (
        client.get(
            path,
            headers={"X-Cheese-Token": mint_scoped_token(project_id=p, topic_id=t)},
        ).status_code
        == 403
    )
    other_p, _ = project(client)
    assert (
        client.get(
            path,
            headers={
                "X-Cheese-Token": mint_scoped_token(project_id=other_p, topic_id=root)
            },
        ).status_code
        == 403
    )
    inspected = client.get(path, headers=headers)
    assert inspected.status_code == 200, inspected.text
    original = inspected.json()["data"]["config"]
    binding = SimpleNamespace(device_id="machine")
    monkeypatch.setattr(
        routes,
        "sql_device_service",
        lambda db: SimpleNamespace(topic_binding=AsyncMock(return_value=binding)),
    )
    monkeypatch.setattr(routes.device_hub, "is_online", lambda device_id: True)
    state = {"state": "failed", "attempt": "first"}
    status = AsyncMock(side_effect=lambda *args, **kwargs: state)
    monkeypatch.setattr(routes, "environment_status", status)
    monkeypatch.setattr(routes.device_hub, "screens_for_topic", lambda tid: [])
    body = {
        "incident_id": incident,
        "expected_revision": original["revision"],
        "config": {"setup_script": "echo repaired"},
    }
    assert (
        client.post(
            path, headers=headers, json={**body, "expected_revision": "stale"}
        ).status_code
        == 422
    )
    chat._active_turn_ids[uuid.UUID(t)] = uuid.uuid4()
    try:
        assert client.post(path, headers=headers, json=body).status_code == 422
    finally:
        chat._active_turn_ids.pop(uuid.UUID(t))
    state["attempt"] = "different"
    assert client.post(path, headers=headers, json=body).status_code == 422
    state["attempt"] = "first"
    repaired = client.post(path, headers=headers, json=body)
    assert repaired.status_code == 200, repaired.text
    assert runner.submit_kickoff.call_count == 2
    assert client.post(path, headers=headers, json=body).status_code == 422
    unchanged = client.get(f"/projects/{p}/environment", headers=owner).json()["data"]
    assert unchanged["config"]["setup_script"] == ""
    assert (
        client.get(path, headers=headers).json()["data"]["config"]["setup_script"]
        == "echo repaired"
    )

    async def fail_again():
        await report_failure(
            chat, uuid.UUID(p), uuid.UUID(t), {"state": "failed", "attempt": "second"}
        )
        async with chat.session_factory() as db:
            assert (await latest_recovery(db, uuid.UUID(t))).meta[
                "state"
            ] == "needs_help"

    client.portal.call(fail_again)
    assert runner.submit_kickoff.call_count == 2


def test_overview_uses_base_environment_and_workroom_keeps_project_scripts(
    client, monkeypatch
):
    import json

    p, t, root, _, owner, chat, _ = setup_incident(client, monkeypatch)
    client.put(
        f"/projects/{p}/environment",
        headers=owner,
        json={"setup_script": "exit 1", "variables": {"VISIBLE": "value"}},
    )
    client.post(
        f"/projects/{p}/environment/rooms/{t}/apply",
        headers=owner,
        json={"latest": True},
    )

    async def inspect():
        provider = SimpleNamespace(builds_model_env=True)
        root_args, _ = await chat._model_kwargs(uuid.UUID(p), provider, uuid.UUID(root))
        room_args, _ = await chat._model_kwargs(uuid.UUID(p), provider, uuid.UUID(t))
        assert json.loads(root_args["env"]["CHEESE_ENVIRONMENT"])["setup_script"] == ""
        assert json.loads(root_args["env"]["CHEESE_ENVIRONMENT"])["variables"] == {}
        assert (
            json.loads(room_args["env"]["CHEESE_ENVIRONMENT"])["setup_script"]
            == "exit 1"
        )

    client.portal.call(inspect)


def test_lost_dispatch_becomes_visible_and_queued_work_is_not_mistaken_for_loss(
    client, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    p, t, _, _, owner, chat, runner = setup_incident(client, monkeypatch)

    async def age_dispatch():
        async with chat.session_factory() as db:
            event = await latest_recovery(db, uuid.UUID(t))
            event.meta = {
                **event.meta,
                "dispatched_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            }
            await db.commit()

    client.portal.call(age_dispatch)
    path = f"/projects/{p}/environment/rooms/{t}"
    assert (
        client.get(path, headers=owner).json()["data"]["recovery_state"] == "requested"
    )
    runner.kickoff_pending = lambda turn_id: False
    assert (
        client.get(path, headers=owner).json()["data"]["recovery_state"] == "needs_help"
    )


def test_overview_can_record_missing_credentials_without_restarting(
    client, monkeypatch
):
    p, t, root, incident, _, _, runner = setup_incident(client, monkeypatch)
    path = f"/projects/{p}/environment/recovery/rooms/{t}"
    headers = {"X-Cheese-Token": mint_scoped_token(project_id=p, topic_id=root)}
    config = client.get(path, headers=headers).json()["data"]["config"]
    result = client.post(
        path,
        headers=headers,
        json={
            "incident_id": incident,
            "expected_revision": config["revision"],
            "reason": "安装需要私有仓库的访问凭据",
            "config": None,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["state"] == "needs_help"
    assert runner.submit_kickoff.call_count == 1


def test_real_failed_turn_preserves_overview_and_room_messages(
    client, stub_hooks, monkeypatch
):
    import asyncio

    from app.domain.agent.device_provider import EnvironmentPreparationError
    from tests.conftest import settle_turn

    p, _ = project(client)
    t = uuid.UUID(room(client, p))
    chat = client.app.dependency_overrides[get_chat_service]()
    original = stub_hooks.ensure_ready

    async def fail_room(**kwargs):
        if kwargs["topic_id"] == t:
            raise EnvironmentPreparationError(
                {"state": "failed", "attempt": "actual", "stage": "setup"}
            )
        return await original(**kwargs)

    monkeypatch.setattr(stub_hooks, "ensure_ready", fail_room)

    async def exercise():
        async with chat.session_factory() as db:
            root = (await db.get(Project, uuid.UUID(p))).root_topic_id
        for topic, content in (
            (root, "overview backlog marker"),
            (t, "room backlog marker"),
        ):
            async for _ in chat.converse(
                topic_id=topic, author="alice", content=content, summon=False
            ):
                pass
        async for _ in chat.kickoff(topic_id=t, prompt="start work"):
            pass
        for _ in range(500):
            if "/environment/recovery/rooms/" in (stub_hooks.last_prompt or ""):
                break
            await asyncio.sleep(0.01)
        assert "overview backlog marker" in stub_hooks.last_prompt
        assert f"/environment/recovery/rooms/{t}" in stub_hooks.last_prompt
        await settle_turn(chat, root)
        monkeypatch.setattr(stub_hooks, "ensure_ready", original)
        async for _ in chat.kickoff(topic_id=t, prompt="continue after repair"):
            pass
        assert "room backlog marker" in stub_hooks.last_prompt
        assert "continue after repair" in stub_hooks.last_prompt
        await settle_turn(chat, t)

    client.portal.call(exercise)
