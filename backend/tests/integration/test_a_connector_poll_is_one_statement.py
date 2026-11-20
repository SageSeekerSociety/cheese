"""A connector's poll for what runs on its machine is one statement.

Every connector polls ``/connector/device/running-sessions`` every few seconds
per machine; it is the busiest route there is. Its token was looked up in a
statement of its own before the sessions were read, and each statement costs
about as much as the rest of the request.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.domain.agent.models import AgentTurn
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.models import ModelCredentialHost
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User
from app.domain.user.tokens import digest_token
from tests.integration.conftest import a_team, registered, session_auth_headers


async def _polled(client, token):
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "after_cursor_execute", record)
    try:
        response = await client.get(
            "/connector/device/running-sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        event.remove(Engine, "after_cursor_execute", record)
    return response, seen


@pytest.mark.anyio
async def test_a_connector_poll_is_one_statement(python_client, stub_project_forge):
    client = python_client
    now = datetime.now(UTC)
    async with client.test_factory() as db:
        await registered(db, "alice")
        team_id = await a_team(db, "alice")
        await db.commit()
    project = (
        await client.post(
            "/projects",
            json={"name": "Polls", "team_id": team_id},
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
    async with client.test_factory() as db:
        user = await db.scalar(select(User).where(User.username == "alice"))
        devices = sql_device_service(db)
        device = await devices.approve(
            await devices.start("workstation"),
            owner_user_id=user.id,
            team_id=project["team_id"],
            supply=Supply.self_hosted,
        )
        db.add(
            ModelCredentialHost(
                id="host-1", token_digest=digest_token("host-token"), name="host"
            )
        )
        sessions = AgentSessionService(db)
        for handle, center in [("worker", device.device_id), ("hosted", "host-1")]:
            session = await sessions.ensure(
                uuid.UUID(room["id"]), handle, harness="claude-code"
            )
            session.runtime_location = {"device_id": center}
            turn_id = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn_id,
                    continuation_id=turn_id,
                    conversation_id=uuid.UUID(room["id"]),
                    agent_handle=handle,
                    author="alice",
                    started_at=now,
                )
            )
        await db.commit()
        device_token = await devices.issue_token(device.device_id)
        await db.commit()

    response, seen = await _polled(client, device_token)
    assert response.status_code == 200, response.text
    assert [row["agent_name"] for row in response.json()["sessions"]] == ["worker"]
    assert response.json()["sessions"][0]["roles"] == ["center"]
    assert len(seen) == 1, seen

    hosted, seen = await _polled(client, "host-token")
    assert hosted.status_code == 200, hosted.text
    assert [row["agent_name"] for row in hosted.json()["sessions"]] == ["hosted"]
    assert len(seen) == 1, seen

    refused, seen = await _polled(client, "not-a-token")
    assert refused.status_code == 401
    assert len(seen) == 1, seen

    async with client.test_factory() as db:
        for turn in await db.scalars(
            select(AgentTurn).where(AgentTurn.stopped_at.is_(None))
        ):
            turn.stopped_at = now
        await db.commit()
    idle, seen = await _polled(client, device_token)
    assert idle.status_code == 200, idle.text
    assert idle.json() == {"sessions": []}
    assert len(seen) == 1, seen
