"""A message waits while its team connection's credential device is away.

The periodic scan starts the waiting message once the device comes back.
The room is told once per message, rather than once per scan.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent import initial_admission
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)
from tests.support.devices import enrollment_team


class _Hub:
    def __init__(self):
        self.online: set[str] = set()

    def is_online(self, device):
        return device in self.online


def _room(client):
    project = post_project(client, json={"name": "Away"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    message = post_message(client, room["id"], "alice", {"content": "hello"})
    return uuid.UUID(project["id"]), uuid.UUID(room["id"]), uuid.UUID(message["id"])


async def _own_agent_and_laptop(factory, project_id, topic_id):
    async with factory() as db:
        alice = await db.scalar(select(User).where(User.username == "alice"))
        instance = AgentInstance(
            project_id=project_id,
            handle=f"own-{uuid.uuid4().hex[:8]}",
            configuration={},
            display_name="alice的 Claude Code",
        )
        db.add(instance)
        await db.flush()
        devices = sql_device_service(db)
        code = await devices.start("laptop")
        laptop = (
            await devices.approve(
                code,
                owner_user_id=alice.id,
                supply=Supply.self_hosted,
                team_id=await enrollment_team(devices, alice.id),
            )
        ).device_id
        db.add(
            DeviceClaudeLoginRow(
                team_id=(await db.get(Project, project_id)).team_id,
                allowed_models=["sonnet"],
                device_id=laptop,
                installed=True,
                logged_in=True,
                auth_method="claude.ai",
                checked_at=datetime.now(UTC),
            )
        )
        await db.flush()
        db.add(
            OwnAgent(
                instance_id=instance.id,
                owner_user_id=alice.id,
                harness="claude-code",
                login_id=laptop,
            )
        )
        seat = await AgentInstanceService(db).ensure_identity(instance)
        await TopicMemberService(db).ensure_agent_seat(topic_id, seat)
        await db.commit()
        return instance.id, laptop


async def _admit(chat, topic_id, block_id, instance_id):
    async with initial_admission.admitted_initial(
        chat.session_factory,
        chat.live.seat_lock_for,
        chat._turn_seat_handle,
        chat.post_system_event,
        topic_id,
        None,
        draining=chat,
        user_block_id=block_id,
        recipient_instance_id=instance_id,
    ) as held:
        return held


def test_a_message_waits_for_its_agents_computer_and_the_room_is_told_once(
    client, monkeypatch
):
    project_id, topic_id, block_id = _room(client)
    hub = _Hub()
    monkeypatch.setattr(initial_admission, "device_hub", hub)

    async def run():
        factory = client.test_request_factory
        instance_id, laptop = await _own_agent_and_laptop(factory, project_id, topic_id)
        chat = client.app.dependency_overrides[get_chat_service]()

        assert await _admit(chat, topic_id, block_id, instance_id) is True
        assert await _admit(chat, topic_id, block_id, instance_id) is True
        async with factory() as db:
            blocks = await db.scalars(
                select(Block).where(Block.conversation_id == topic_id)
            )
            told = [b for b in blocks if "电脑上线后自动继续" in (b.content or "")]
        assert len(told) == 1, "told once, not once per scan"

        hub.online.add(laptop)
        assert await _admit(chat, topic_id, block_id, instance_id) is False

    client.portal.call(run)
