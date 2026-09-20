"""地点解析的入口是会话，不是房间。

两条验收（PLAN P18）：

* 同一条会话上的所有轮次拿到同一个租约句柄，同一个房间里的两条会话拿到各自的——
  后者在改动之前会撞上「执行机器与本房间已经记录的位置不一致」直接报错，因为整个
  房间只有一条 ``topics.session_placement``。
* 把一条会话搬到另一台会话机，下一轮它仍然引用得上上一轮自己说过的话：续接凭证
  和位置是会话行上的两样东西，换机器动的是后者。
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent_session.services import AgentSessionService

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
def room(client):
    project = client.post(
        "/projects", json={"name": "Two teammates", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    return uuid.UUID(project["id"]), uuid.UUID(topic["id"])


def channel(client, monkeypatch, executors=("executor",)):
    """A central channel whose executor resolution can differ per turn."""
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    online = {"center", "center-two", *executors}
    hub: Any = SimpleNamespace(
        is_online=lambda device: device in online,
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INSTALLED)}),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_factory)
    executor._device_api_base = AsyncMock(return_value="http://execution-api")
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    central._wait_executor = AsyncMock()
    return central


async def _open(central, project, topic, agent, executor):
    """One turn of one agent's session, through the resolution entry."""
    await central.ensure_ready(
        session=SessionRef(project, topic, agent, "claude-code"),
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System"),
        precheck=(executor, 1, agent),
    )


@pytest.mark.anyio
async def test_two_sessions_in_one_room_hold_their_own_leases(
    client, room, monkeypatch
):
    """一个房间两条会话，各租各的手；同一条会话的下一轮还是那一份。"""
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a", "hands-b"))

    await _open(central, project, topic, "ada", "hands-a")
    await _open(central, project, topic, "linus", "hands-b")

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        ada = await sessions.place(topic, "ada")
        linus = await sessions.place(topic, "linus")
    assert ada is not None and linus is not None
    assert ada.lease["device_id"] == "hands-a"
    assert linus.lease["device_id"] == "hands-b"

    # Every later turn of one session resolves the lease that session already
    # holds — it is not re-rented, and it is not the other session's.
    central._hub.call_executor.return_value = {
        **INSTALLED,
        "pid": 123,
        "capabilities": ["prepare"],
        "context_tree": {"generation": "fixture", "entries": {}},
    }
    await _open(central, project, topic, "ada", "hands-a")
    async with client.test_factory() as db:
        again = await AgentSessionService(db).place(topic, "ada")
    assert again is not None
    assert again.lease["device_id"] == "hands-a"
    assert again.resource_id == ada.resource_id


@pytest.mark.anyio
async def test_a_session_that_moves_machine_keeps_what_it_said(client, room):
    """搬一台会话机，上一轮说过的话还在这条会话名下。"""
    project, topic = room
    del project
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        await sessions.remember_place(
            topic_id=topic,
            agent_handle="ada",
            work_lease={"kind": "device", "device_id": "hands-a"},
            runtime_location={
                "device_id": "center",
                "resource_id": str(topic),
                "channel": "device",
            },
        )
        await sessions.remember(
            topic_id=topic, agent_handle="ada", resume_token="conversation-1"
        )
        await db.commit()

    async with client.test_factory() as db:
        await AgentSessionService(db).remember_place(
            topic_id=topic,
            agent_handle="ada",
            work_lease={"kind": "device", "device_id": "hands-a"},
            runtime_location={
                "device_id": "center-two",
                "resource_id": str(topic),
                "channel": "device",
            },
        )
        await db.commit()

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        moved = await sessions.place(topic, "ada")
        resumes_by = await sessions.resume_token(topic, "ada")
    assert moved is not None
    assert moved.machine == "center-two"
    assert resumes_by == "conversation-1"


@pytest.mark.anyio
async def test_a_room_with_no_resume_token_yet_has_not_run(client, room):
    """租到机器还不算跑过——算力设置要到这条会话说出第一句才冻住。"""
    project, topic = room
    del project
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        await sessions.remember_place(
            topic_id=topic,
            agent_handle="ada",
            work_lease=None,
            runtime_location={
                "device_id": "center",
                "resource_id": str(topic),
                "channel": "device",
            },
        )
        await db.commit()
        assert await sessions.has_run(topic) is False
        await sessions.remember(
            topic_id=topic, agent_handle="ada", resume_token="conversation-1"
        )
        await db.commit()
        assert await sessions.has_run(topic) is True
