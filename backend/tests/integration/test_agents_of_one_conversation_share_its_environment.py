"""Two agents of one conversation with the same choice work in one environment.

A conversation points to its environment: every agent whose choice there is
the conversation's default finds that one, so their tool calls land in the
same directory on the same machine and see each other's files. An agent that
chose another environment for itself keeps its own.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers
from tests.support.devices import enrollment_team, place_in_project_team

pytestmark = pytest.mark.anyio


async def _computers(db, project_id, names):
    devices = sql_device_service(db)
    owner = await db.scalar(select(User).where(User.username == "alice"))
    if owner is None:
        owner = User(
            username="alice",
            email="alice@example.test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(owner)
        await db.flush()
    made = []
    for name in names:
        device = await devices.approve(
            await devices.start(name),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(devices, owner.id),
        )
        await place_in_project_team(
            devices, device.device_id, project_id, actor_user_id=owner.id
        )
        made.append(device.device_id)
    return made


async def test_agents_with_the_conversations_default_work_in_one_environment(
    client, monkeypatch
):
    project = post_project(client, json={"name": "Shared hands"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        shared, own = await _computers(db, project_id, ("shared", "own"))
        topic = await db.get(Topic, topic_id)
        # The room's default: one computer of the project's.
        topic.default_environment = {"kind": "self_hosted", "device_id": shared}
        resource = str(topic.resource_id or topic_id)
        actor = (await IdentityService(db).ensure_room_agent_user(topic_id)).username
        identities = {}
        for handle in ("ada", "bob", "cy"):
            session = await AgentSessionService(db).ensure(
                topic_id, handle, harness="claude-code"
            )
            session.runtime_location = {
                "device_id": "center",
                "resource_id": resource,
                "channel": "device",
            }
            identities[handle] = (
                session.id,
                bind_resource_token(
                    mint_scoped_token(
                        project_id=str(project_id),
                        topic_id=str(topic_id),
                        agent_handle=actor,
                    ),
                    resource,
                    session_id=str(session.id),
                ),
            )
        # Cy chose a computer of its own.
        await AgentSessionService(db).set_environment(
            topic_id,
            "cy",
            {"kind": "self_hosted", "device_id": own},
            chosen_by=None,
        )
        await db.commit()

    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {"state": f"/{device}/state", "workspace": "/work", "mcp_servers": []}
            ),
        }

    monkeypatch.setattr(
        session_work,
        "device_hub",
        SimpleNamespace(
            target=lambda _device: "linux-amd64",
            is_online=lambda device: True,
            reconnecting=lambda device: False,
            exec=AsyncMock(side_effect=install),
        ),
    )

    def executor_answer(target, method, *args, **_kwargs):
        if method == "ping":
            from app.domain.agent.harness.claude_code.remote_execution import (
                launch,
                runtime,
            )

            return {
                "capabilities": ["prepare"],
                "sandbox": False,
                "protocol_version": runtime.PROTOCOL_VERSION,
                "files": {
                    name: hashlib.sha256(value.encode()).hexdigest()
                    for name, value in launch.file_sources().items()
                },
            }
        if method == "prepare":
            return target
        return {}

    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=executor_answer))

    hands = {}
    for handle, (session_id, token) in identities.items():
        response = client.post(
            f"/topics/{topic_id}/sessions/{session_id}/work-lease",
            headers={"Authorization": f"Bearer {token}"},
            json={"env": {}},
        )
        assert response.status_code == 200, response.text
        hands[handle] = response.json()["data"]["target"]

    # Ada and Bob have the room's default: one machine, one directory there.
    assert hands["ada"]["device_id"] == hands["bob"]["device_id"] == shared
    assert hands["ada"]["home"] == hands["bob"]["home"]
    # Cy works where it chose, in a directory of its own.
    assert hands["cy"]["device_id"] == own
    assert hands["cy"]["home"] != hands["ada"]["home"]
