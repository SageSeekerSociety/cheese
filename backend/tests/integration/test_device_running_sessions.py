"""A machine viewer sees active work and roles, never another team's work."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.common.auth import create_access_token
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.team.models import TeamMemberRole, TeamUserRelation
from app.domain.user.models import User
from tests.integration.conftest import a_team, registered, session_auth_headers


@pytest.mark.anyio
async def test_running_sessions_roles_visibility_and_completion(
    python_client, stub_project_forge
):
    client = python_client
    now = datetime.now(UTC)
    async with client.test_factory() as db:
        alice = create_access_token(await registered(db, "alice"), handle="alice")
        bob_id = await registered(db, "bob")
        bob = create_access_token(bob_id, handle="bob")
        team_id = await a_team(db, "alice")
        db.add(
            TeamUserRelation(
                team_id=team_id,
                user_id=bob_id,
                role=TeamMemberRole.MEMBER,
                created_at=now,
                updated_at=now,
            )
        )
        outsider = create_access_token(
            await registered(db, "outsider"), handle="outsider"
        )
        await db.commit()
    project = (
        await client.post(
            "/projects",
            json={"name": "Sessions", "team_id": team_id},
            headers=session_auth_headers("alice"),
        )
    ).json()["data"]
    room = (
        await client.post(
            "/topics",
            json={
                "project_id": project["id"],
                "title": "Planning",
                "members_only": True,
            },
            headers=session_auth_headers("alice"),
        )
    ).json()["data"]
    public_room = (
        await client.post(
            "/topics",
            json={"project_id": project["id"], "title": "Shared planning"},
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
    async with client.test_factory() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("workstation"),
            owner_user_id=user.id,
            team_id=project["team_id"],
            supply=Supply.self_hosted,
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
            ("public", device.device_id, "elsewhere", None),
        ]:
            conversation_id = uuid.UUID(
                public_room["id"] if handle == "public" else room["id"]
            )
            session = await sessions.ensure(
                conversation_id, handle, harness="claude-code"
            )
            session.runtime_location = {"device_id": center}
            session.work_lease = {"device_id": executor}
            turn_id = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn_id,
                    continuation_id=turn_id,
                    conversation_id=conversation_id,
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
        device_token = await devices.issue_token(device.device_id)
        assert device_token
        await db.commit()
        device_id = device.device_id

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
        "public": ["center"],
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
    bob_headers = {"Authorization": f"Bearer {bob}"}
    assert (
        await client.get(f"/topics/{room['id']}", headers=bob_headers)
    ).status_code == 404
    visible = await client.get(url, headers=bob_headers)
    assert visible.status_code == 200, visible.text
    assert [row["agent_name"] for row in visible.json()["sessions"]] == ["public"]
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
