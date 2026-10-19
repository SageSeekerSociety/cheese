"""A machine viewer sees active work and roles, never another team's work."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.wiring import sql_device_service
from app.domain.device.supply import Supply
from app.domain.user.models import User
from tests.integration.conftest import registered, session_auth_headers


@pytest.mark.anyio
async def test_running_sessions_roles_visibility_and_completion(
    python_client, stub_project_forge
):
    client = python_client
    async with client.test_factory() as db:
        alice = create_access_token(await registered(db, "alice"), handle="alice")
        outsider = create_access_token(
            await registered(db, "outsider"), handle="outsider"
        )
        await db.commit()
    project = (
        await client.post(
            "/projects",
            json={"name": "Sessions"},
            headers=session_auth_headers("alice"),
        )
    ).json()["data"]
    room = (
        await client.post(
            "/topics",
            json={"project_id": project["id"], "title": "Planning"},
            headers=session_auth_headers("alice"),
        )
    ).json()["data"]
    task = (
        await client.post(
            f"/topics/{room['id']}/tasks",
            json={"title": "Review changes"},
            headers=session_auth_headers("alice"),
        )
    ).json()["data"]
    now = datetime.now(UTC)
    async with client.test_factory() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("workstation"),
            owner_user_id=user.id,
            supply=Supply.self_hosted,
        )
        await devices.assign_to_team(
            device.device_id, project["team_id"], actor_user_id=user.id
        )
        sessions = AgentSessionService(db)
        db.add(
            AgentInstance(
                project_id=uuid.UUID(project["id"]),
                handle="both",
                display_name="Ada",
                configuration={},
            )
        )
        for handle, center, executor, stopped in [
            ("both", device.device_id, device.device_id, None),
            ("center", device.device_id, "elsewhere", None),
            ("executor", "elsewhere", device.device_id, None),
            ("finished", device.device_id, device.device_id, now),
            ("other", "elsewhere", "elsewhere", None),
        ]:
            session = await sessions.ensure(
                uuid.UUID(room["id"]), handle, harness="claude-code"
            )
            session.runtime_location = {"device_id": center}
            session.work_lease = {"device_id": executor}
            turn_id = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn_id,
                    continuation_id=turn_id,
                    conversation_id=uuid.UUID(room["id"]),
                    agent_handle=handle,
                    author="alice",
                    started_at=now,
                    stopped_at=stopped,
                )
            )
        session = await sessions.ensure(
            uuid.UUID(task["id"]), "task-worker", harness="claude-code"
        )
        session.runtime_location = {"device_id": device.device_id}
        turn_id = uuid.uuid4()
        db.add(
            AgentTurn(
                id=turn_id,
                continuation_id=turn_id,
                conversation_id=uuid.UUID(task["id"]),
                agent_handle="task-worker",
                author="alice",
                started_at=now,
            )
        )
        await db.commit()
        device_id, device_token = device.device_id, device.token

    url = f"/connector/devices/{device_id}/running-sessions"
    assert (await client.get(url)).status_code == 401
    assert (
        await client.get(url, headers={"Authorization": f"Bearer {outsider}"})
    ).status_code == 403
    response = await client.get(url, headers={"Authorization": f"Bearer {alice}"})
    assert response.status_code == 200, response.text
    rows = response.json()["sessions"]
    assert {row["agent_name"]: row["roles"] for row in rows} == {
        "Ada": ["center", "executor"],
        "center": ["center"],
        "executor": ["executor"],
        "task-worker": ["center"],
    }
    assert all(
        row["project_name"] == "Sessions" and row["since"] == now.isoformat()
        for row in rows
    )
    by_name = {row["agent_name"]: row for row in rows}
    assert by_name["Ada"]["conversation_title"] == "Planning"
    assert by_name["task-worker"]["conversation_title"] == "Review changes"
    assert by_name["task-worker"]["conversation_kind"] == "task"
    assert by_name["task-worker"]["conversation_id"] == task["id"]
    assert by_name["task-worker"]["room_id"] == room["id"]
    local = await client.get(
        "/connector/device/running-sessions", headers={"X-Cheese-Session": device_token}
    )
    assert local.status_code == 200, local.text
    assert local.json() == response.json()
    assert (
        await client.get(
            "/connector/device/running-sessions", headers={"X-Cheese-Session": "wrong"}
        )
    ).status_code == 401

    async with client.test_factory() as db:
        for turn in await db.scalars(
            select(AgentTurn).where(AgentTurn.stopped_at.is_(None))
        ):
            turn.stopped_at = now
        await db.commit()
    assert (
        await client.get(url, headers={"Authorization": f"Bearer {alice}"})
    ).json() == {"sessions": []}
