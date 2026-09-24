"""Session-owned placement and continuation identity.

Real admission and tool acquisition keep roommates' work leases separate.
Approved hands replacement and a recorded session-host change preserve the
resume token passed to the next launch. The latter is metadata coverage;
transcript hydration and actual model recall are outside this fixture.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import execution
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef, deployment_harness
from app.domain.agent.harness.claude_code import ClaudeCodeChannel, ClaudeCodeRuntime
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.machine import session_work
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
async def room(client):
    project = post_project(
        client, json={"name": "Two teammates", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    project_id = uuid.UUID(project["id"])
    for handle in ("ada", "linus"):
        made = client.post(f"/projects/{project_id}/agents", json={"handle": handle})
        assert made.status_code == 200, made.text
    async with client.test_factory() as db:
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
        devices = sql_device_service(db)
        assigned = {}
        for name in ("hands-a", "hands-b"):
            code = await devices.start(name)
            device = await devices.approve(
                code,
                owner_user_id=owner.id,
                supply=Supply.self_hosted,
                visibility=Visibility.host,
            )
            await devices.assign_to_project(
                device.device_id, project_id, actor_user_id=owner.id
            )
            assigned[name] = device.device_id
        await db.commit()
    client.session_test_devices = assigned
    return project_id, uuid.UUID(topic["id"])


def channel(client, monkeypatch, executors=("executor",)):
    """A central channel whose executor resolution can differ per turn."""
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    online = {"center", "center-two", *client.session_test_devices.values()}
    hub: Any = SimpleNamespace(
        is_online=lambda device: device in online,
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INSTALLED)}),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    executor._device_api_base = AsyncMock(return_value="http://execution-api")
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    central.test_client = client
    monkeypatch.setattr(session_work, "device_hub", hub)
    monkeypatch.setattr(execution, "call", AsyncMock(return_value={"tasks": []}))
    return central


def _open(central, project, topic, agent, executor, *, resume=None):
    """Admit a real session, then acquire its hands through the tool endpoint."""
    client = central.test_client

    async def prepare():
        ref = SessionRef(project, topic, agent, harness="claude-code")
        precheck = await central.precheck(ref, needs_place=True)
        async with client.test_request_factory() as db:
            row = await AgentSessionService(db).ensure(
                topic, agent, harness="claude-code"
            )
            if row.execution_request is None:
                row.execution_request = {
                    "generation": str(uuid.uuid4()),
                    "choice": {
                        "name": executor,
                        "profile": "device",
                        "device_id": client.session_test_devices[executor],
                    },
                }
            await db.commit()
        await central.ensure_ready(
            session=ref,
            token=mint_scoped_token(
                project_id=str(project),
                topic_id=str(topic),
                agent_handle=precheck.agent_handle,
            ),
            env={},
            launch=ClaudeLaunch("System", resume_session_id=resume),
            precheck=precheck,
        )
        return central._ensure_screen.await_args.kwargs

    opening = client.portal.call(prepare)
    target = json.loads(opening["env"]["CHEESE_EXECUTION_TARGET"])
    assert target["kind"] == "deferred"
    response = client.post(
        target["lease_path"],
        headers={"X-Cheese-Token": opening["token"]},
        json={"env": {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["target"]


@pytest.mark.anyio
async def test_two_sessions_in_one_room_hold_their_own_leases(
    client, room, monkeypatch
):
    """一个房间两条会话，各租各的手；同一条会话的下一轮还是那一份。"""
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a", "hands-b"))

    _open(central, project, topic, "ada", "hands-a")
    _open(central, project, topic, "linus", "hands-b")

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        ada = await sessions.place(topic, "ada", harness=deployment_harness())
        linus = await sessions.place(topic, "linus", harness=deployment_harness())
    assert ada is not None and linus is not None
    assert ada.lease["device_id"] == client.session_test_devices["hands-a"]
    assert linus.lease["device_id"] == client.session_test_devices["hands-b"]

    # Every later turn of one session resolves the lease that session already
    # holds — it is not re-rented, and it is not the other session's.
    central._hub.call_executor.return_value = {
        **INSTALLED,
        "pid": 123,
        "capabilities": ["prepare"],
        "context_tree": {"generation": "fixture", "entries": {}},
    }
    _open(central, project, topic, "ada", "hands-a")
    async with client.test_factory() as db:
        again = await AgentSessionService(db).place(
            topic, "ada", harness=deployment_harness()
        )
    assert again is not None
    assert again.lease["device_id"] == client.session_test_devices["hands-a"]
    assert again.resource_id == ada.resource_id


@pytest.mark.anyio
async def test_a_session_that_moves_machine_keeps_what_it_said(
    client, room, monkeypatch
):
    """Replacing hands preserves the stored resume token passed to the harness.

    This tests continuation identity and recorded session placement, not transcript
    hydration or the contents of a real model conversation.
    """
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a",))

    _open(central, project, topic, "ada", "hands-a")
    # 骨架交回一个可续的 token——写侧和真正跑完一轮时走的是同一个入口。
    async with client.test_factory() as db:
        await AgentSessionService(db).remember(
            topic_id=topic,
            agent_handle="ada",
            resume_token="conversation-1",
            harness=deployment_harness(),
        )
        await db.commit()

    # Hands change through the authenticated selection API. The native
    # session host and its files stay in place; this is not host-loss recovery.
    async with client.test_factory() as db:
        row = await AgentSessionService(db).ensure(
            topic, "ada", harness=deployment_harness()
        )
        session_id = row.id
        before_resource = row.work_lease["resource_id"]
    path = f"/topics/{topic}/sessions/{session_id}/work-choice"
    headers = session_auth_headers("alice")
    proposal = client.put(
        path,
        headers=headers,
        json={
            "choice": {
                "name": "Second hands",
                "profile": "device",
                "device_id": client.session_test_devices["hands-b"],
            }
        },
    )
    assert proposal.status_code == 200, proposal.text
    central._hub.call_executor.return_value = {
        **INSTALLED,
        "pid": 123,
        "capabilities": ["prepare"],
        "context_tree": {"generation": "fixture", "entries": {}},
    }
    # 下一轮：续接指针是从这条会话行上读出来的，和 `chat.py` 读的是同一处。
    async with client.test_factory() as db:
        resumes_by = await AgentSessionService(db).resume_token(
            topic, "ada", harness=deployment_harness()
        )
    target = _open(central, project, topic, "ada", "hands-b", resume=resumes_by)
    assert target["device_id"] == client.session_test_devices["hands-b"]
    assert target["resource_id"] != before_resource

    opened = central._ensure_screen.await_args.kwargs
    assert opened["device_id"] == "center", opened
    assert opened["launch"].resume_session_id == "conversation-1", opened

    # Separately verify the recorded host is respected. Updating placement is
    # only metadata here: no claim is made that transcripts reached that host.
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        before = await sessions.place(topic, "ada", harness=deployment_harness())
        await sessions.remember_place(
            topic_id=topic,
            agent_handle="ada",
            work_lease=before.lease,
            runtime_location={
                "device_id": "center-two",
                "resource_id": before.resource_id,
                "channel": before.channel,
            },
            harness=deployment_harness(),
        )
        await db.commit()
        resumes_by = await sessions.resume_token(
            topic, "ada", harness=deployment_harness()
        )
    on_new_host = _open(central, project, topic, "ada", "hands-b", resume=resumes_by)

    opened = central._ensure_screen.await_args.kwargs
    assert opened["device_id"] == "center-two"
    assert opened["launch"].resume_session_id == "conversation-1"
    assert on_new_host["resource_id"] == target["resource_id"]


@pytest.mark.anyio
async def test_a_room_with_no_resume_token_yet_has_not_run(business_db_factory, room):
    """租到机器还不算跑过——算力设置要到这条会话说出第一句才冻住。"""
    project, topic = room
    del project
    async with business_db_factory() as db:
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
            harness=deployment_harness(),
        )
        await db.commit()
        assert await sessions.has_run(topic) is False
        await sessions.remember(
            topic_id=topic,
            agent_handle="ada",
            resume_token="conversation-1",
            harness=deployment_harness(),
        )
        await db.commit()
        assert await sessions.has_run(topic) is True


@pytest.mark.anyio
async def test_a_room_that_switched_harness_is_claimed_by_one_channel(
    client, room, monkeypatch
):
    """换过骨架的房间，冷启动只归最后落位的那条会话——别的骨架一概不认领。

    会话行按 (房间, agent, 骨架) 各占一行，而换骨架的时候没有任何地方去把旧那行
    的位置清空，所以这样的房间带着两行非空的 ``runtime_location``。房间的屏只有
    一块：认错了，就是拿 Claude Code 的拼装器去翻译 pi 说的话，再当成自己的报进
    房间。

    认领的判据在 runtime 那一侧，是它自己的骨架——通道答不出这个，一条
    ``CentralChannel`` 同时被几个骨架的 runtime 包着（``build_compute_pool``）。
    所以通道只把落在自己这儿的屏原样认回来（``CentralChannel.restore``），
    哪条会话归谁由各骨架按会话行自己认。
    """
    project, topic = room
    for harness in ("claude-code", "pi"):
        async with client.test_factory() as db:
            await AgentSessionService(db).remember_place(
                topic_id=topic,
                agent_handle="ada",
                harness=harness,
                work_lease=None,
                runtime_location={
                    "device_id": "center",
                    "resource_id": str(topic),
                    "channel": "device",
                    "runtime": {"harness": harness, "state": INSTALLED["state"]},
                },
            )
            await db.commit()

    async with client.test_factory() as db:
        placed = await AgentSessionService(db).placed_sessions()
    assert [(row[1], row[3]) for row in placed] == [(topic, "pi")]

    central = channel(client, monkeypatch)
    central.restore_screens = AsyncMock()

    client.portal.call(central.restore)
    central.restore_screens.assert_awaited_once_with([(project, topic, "center")])
    # 这块屏是 pi 开的，所以 Claude Code 那一侧一条都不认领。
    runtime = ClaudeCodeRuntime(ClaudeCodeChannel(central))
    assert client.portal.call(runtime.recover) == []
