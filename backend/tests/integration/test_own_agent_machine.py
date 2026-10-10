"""A member's own Claude Code works on its owner's machine (#2991).

Its session runs on one of its owner's computers, with the login the owner gave
the platform there, and its hands — the files, the commands — are that same
computer: whatever the room chose, and whether or not that computer serves the
project. No other member's computer is ever its hands.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.sandbox_auth import bind_resource_token, mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.owner_provider import OWNER_CHANNEL, OwnerChannel
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import session_work as work_lease
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers

pytestmark = pytest.mark.anyio


async def _user(db, handle):
    user = await db.scalar(select(User).where(User.username == handle))
    if user is None:
        user = User(
            username=handle,
            email=f"{handle}@example.test",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
    return user


async def _machine(devices, name, owner, project_id=None):
    code = await devices.start(name)
    device = await devices.approve(
        code, owner_user_id=owner.id, supply=Supply.self_hosted
    )
    if project_id is not None:
        await devices.assign_to_project(
            device.device_id, project_id, actor_user_id=owner.id
        )
    return device.device_id


def _hub():
    async def install(device, argv, **kwargs):
        return {
            "exit": 0,
            "stdout": json.dumps(
                {
                    "state": f"/{device}/state",
                    "workspace": f"/{device}/work",
                    "mcp_servers": [],
                }
            ),
        }

    return SimpleNamespace(
        target=lambda _device: "linux-amd64",
        isolates=lambda _device: None,
        is_online=lambda device: True,
        reconnecting=lambda device: False,
        exec=AsyncMock(side_effect=install),
    )


def _executor(target, method, *args, **_kwargs):
    if method == "ping":
        import hashlib

        from app.domain.agent.harness.claude_code.remote_execution import (
            launch,
            runtime,
        )

        return {
            "capabilities": ["prepare"],
            "sandbox": True,
            "protocol_version": runtime.PROTOCOL_VERSION,
            "files": {
                name: hashlib.sha256(value.encode()).hexdigest()
                for name, value in launch.file_sources().items()
            },
        }
    if method == "prepare":
        return target
    if method == "control":
        return {"tasks": []}
    return {"device": target["device_id"]}


async def _own_session(client, *, session_host_of):
    """A project whose room works on the room owner's computer, and Bob's own
    Claude Code in it, its session on the machine ``session_host_of`` names."""
    project = post_project(client, json={"name": "Own hands"}, owner="alice").json()[
        "data"
    ]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(room["id"])
    async with client.test_factory() as db:
        devices = sql_device_service(db)
        alice, bob = await _user(db, "alice"), await _user(db, "bob")
        rooms_machine = await _machine(devices, "alices-desk", alice, project_id)
        bobs_laptop = await _machine(devices, "bobs-laptop", bob)
        instance = AgentInstance(
            project_id=project_id,
            handle=f"own-{uuid.uuid4().hex[:8]}",
            configuration={},
            display_name="bob的 Claude Code",
        )
        db.add(instance)
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=instance.id, owner_user_id=bob.id, harness="claude-code"
            )
        )
        # Bob gave the platform his login on his laptop.
        db.add(
            DeviceClaudeLoginRow(
                device_id=bobs_laptop,
                installed=True,
                logged_in=True,
                checked_at=datetime.now(UTC),
            )
        )
        actor = (await IdentityService(db).ensure_room_agent_user(topic_id)).username
        topic = await db.get(Topic, topic_id)
        topic.compute_config = {
            "name": "alices-desk",
            "profile": "device",
            "device_id": rooms_machine,
        }
        resource = str(topic.resource_id or topic_id)
        # A session names its agent by the instance's handle, as a turn does.
        session = await AgentSessionService(db).ensure(
            topic_id, instance.handle, harness="claude-code"
        )
        host = {"bob": bobs_laptop, "alice": rooms_machine}[session_host_of]
        session.runtime_location = {
            "device_id": host,
            "resource_id": resource,
            "channel": OWNER_CHANNEL,
        }
        token = bind_resource_token(
            mint_scoped_token(
                project_id=str(project_id),
                topic_id=str(topic_id),
                agent_handle=actor,
            ),
            resource,
            session_id=str(session.id),
        )
        await db.commit()
    return SimpleNamespace(
        project_id=project_id,
        topic_id=topic_id,
        session_id=session.id,
        token=token,
        bobs_laptop=bobs_laptop,
        handle=instance.handle,
    )


async def test_an_own_agent_works_on_its_owners_machine_not_the_rooms(
    client, monkeypatch
):
    own = await _own_session(client, session_host_of="bob")
    monkeypatch.setattr(work_lease, "device_hub", _hub())
    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=_executor))

    response = client.post(
        f"/topics/{own.topic_id}/sessions/{own.session_id}/work-lease",
        headers={"X-Cheese-Token": own.token},
        json={"env": {}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["target"]["device_id"] == own.bobs_laptop


async def test_an_own_agent_is_never_given_another_members_machine(client, monkeypatch):
    own = await _own_session(client, session_host_of="alice")
    monkeypatch.setattr(work_lease, "device_hub", _hub())
    monkeypatch.setattr(execution, "call", AsyncMock(side_effect=_executor))

    response = client.post(
        f"/topics/{own.topic_id}/sessions/{own.session_id}/work-lease",
        headers={"X-Cheese-Token": own.token},
        json={"env": {}},
    )

    assert response.status_code == 403, response.text


async def test_an_own_agents_conversation_starts_on_its_owners_machine(client):
    own = await _own_session(client, session_host_of="bob")
    channel = OwnerChannel(
        DeviceChannel(hub=_hub(), session_factory=client.test_request_factory)
    )

    placement = client.portal.call(
        lambda: channel.precheck(
            SessionRef(own.project_id, own.topic_id, own.handle, harness="claude-code"),
            needs_place=False,
        )
    )

    assert placement.machine == own.bobs_laptop
