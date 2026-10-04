"""Where a session's hands are: the room's machine, and continuation identity.

一个话题一个容器（2026-09-28 决定，推翻结论 60）：一个房间里的会话都工作在房间那一
项算出来的那台机器上，所以这里问的是「房间的机器落在哪」，不是「这位队友自己挑了哪
台」。Moving the room and a recorded session-host change both preserve the resume
token passed to the next launch. The latter is metadata coverage; transcript
hydration and actual model recall are outside this fixture.
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
from app.domain.agent import execution
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef, harness_for
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.agent_session.services import AgentSessionService
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.machine import session_work
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_central_room_sessions import screen_for, sessions

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
async def room(client):
    project = post_project(
        client, json={"name": "Two teammates"}, owner="alice"
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id = uuid.UUID(project["id"])
    for handle in ("ada", "linus"):
        made = client.post(f"/projects/{project_id}/agents", json={"handle": handle})
        assert made.status_code == 200, made.text
    async with client.test_factory() as db:
        # Both sit in the room: a session runs only for a teammate seated there.
        for handle in ("ada", "linus"):
            db.add(
                TopicMembership(
                    topic_id=uuid.UUID(topic["id"]),
                    member_handle=handle,
                    role=TopicRole.member,
                )
            )
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
        target=lambda _device: "linux-amd64",
        is_online=lambda device: device in online,
        reconnecting=lambda device: False,
        # No session is running on the session host: `_ensure_screen` is
        # stubbed, so every turn starts one.
        all_online_screens=lambda: [],
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INSTALLED)}),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    executor._device_api_base = AsyncMock(return_value="http://execution-api")
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(
        return_value=SimpleNamespace(device_id="center", sid="s1")
    )
    central.test_client = client
    monkeypatch.setattr(session_work, "device_hub", hub)
    # A switch first pushes the session's work on the machine it leaves
    # (`checkpoint`); every other control call reads background work.
    monkeypatch.setattr(
        execution,
        "call",
        AsyncMock(
            side_effect=lambda target, method, params, **_: (
                {"value": {"stdout": ""}}
                if params.get("subtype") == "checkpoint"
                else {"tasks": []}
            )
        ),
    )
    return central


def _room_choice(client, topic, executor):
    """This room works on ``executor`` — the ROOM's choice, not a session's.

    一个话题一个容器（2026-09-28 决定，推翻结论 60）：手落在哪台机器上由房间那一项
    答，会话行上那份只是跟着写的副本，所以这里摆的是房间。
    """
    return {
        "choice": {
            "name": executor,
            "profile": "device",
            "device_id": client.session_test_devices[executor],
        }
    }


async def _on_the_room(client, topic, executor) -> None:
    async with client.test_factory() as db:
        room = await db.get(Topic, topic)
        room.compute_config = _room_choice(client, topic, executor)["choice"]
        await db.commit()


def _open(central, project, topic, agent, *, resume=None):
    """Admit a real session, then acquire its hands through the tool endpoint."""
    client = central.test_client

    async def prepare():
        ref = SessionRef(project, topic, agent, harness="claude-code")
        await screen_for(central, ref, ClaudeLaunch("System", resume_session_id=resume))
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
async def test_two_sessions_in_one_room_land_on_the_rooms_machine(
    client, room, monkeypatch
):
    """一个房间两条会话，落在同一台机器上；同一条会话的下一轮还是那一份。

    一个话题一个容器（2026-09-28 决定，推翻结论 60）：房间里坐着的每一条会话都工作
    在房间那一项算出来的那台机器上，所以第二条会话开工时不会再自己挑一台。
    """
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a", "hands-b"))
    await _on_the_room(client, topic, "hands-a")

    _open(central, project, topic, "ada")
    _open(central, project, topic, "linus")

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        ada = await sessions.place(topic, "ada", harness=harness_for(None))
        linus = await sessions.place(topic, "linus", harness=harness_for(None))
    assert ada is not None and linus is not None
    hands_a = client.session_test_devices["hands-a"]
    assert ada.lease["device_id"] == hands_a
    assert linus.lease["device_id"] == hands_a
    assert ada.machine == linus.machine

    # Every later turn of one session resolves the lease that session already
    # holds — it is not re-rented, and it is not the other session's.
    central._hub.call_executor.return_value = {
        **INSTALLED,
        "pid": 123,
        "capabilities": ["prepare"],
        "context_tree": {"generation": "fixture", "entries": {}},
    }
    _open(central, project, topic, "ada")
    async with client.test_factory() as db:
        again = await AgentSessionService(db).place(
            topic, "ada", harness=harness_for(None)
        )
    assert again is not None
    assert again.lease["device_id"] == hands_a
    assert again.resource_id == ada.resource_id


@pytest.mark.anyio
async def test_an_automatic_room_gives_the_second_session_the_first_ones_machine(
    client, room, monkeypatch
):
    """「系统挑一台」的房间：第二条会话拿到第一条已经在用的那一台。

    One machine per room is the point of the automatic pool too: 两台在线的机器里先来
    的那位随手挑中了一台，后来的人就该站到同一台上——各挑一台正是「一个房间两个队
    友在两台机器上」的那条路（``_roommates_device``）。
    """
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a", "hands-b"))
    await _on_the_room(client, topic, "hands-a")
    async with client.test_factory() as db:
        room_row = await db.get(Topic, topic)
        # 「自动选一台」：房间不点名，机器由第一条会话落定时挑中的那台决定。
        room_row.compute_config = {"name": "自有设备 · 自动选择", "profile": "device"}
        await db.commit()

    _open(central, project, topic, "ada")
    _open(central, project, topic, "linus")

    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        ada = await sessions.place(topic, "ada", harness=harness_for(None))
        linus = await sessions.place(topic, "linus", harness=harness_for(None))
    assert ada is not None and linus is not None
    assert linus.lease["device_id"] == ada.lease["device_id"]


@pytest.mark.anyio
async def test_a_room_that_moves_keeps_what_it_said(client, room, monkeypatch):
    """Replacing the room's machine preserves the stored resume token.

    This tests continuation identity and recorded session placement, not transcript
    hydration or the contents of a real model conversation.
    """
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a", "hands-b"))
    await _on_the_room(client, topic, "hands-a")

    _open(central, project, topic, "ada")
    # 骨架交回一个可续的 token——写侧和真正跑完一轮时走的是同一个入口。
    async with client.test_factory() as db:
        await AgentSessionService(db).remember(
            topic_id=topic,
            agent_handle="ada",
            resume_token="conversation-1",
            harness=harness_for(None),
        )
        await db.commit()

    # The room's machine changes through the room's own endpoint — 一个话题一个容
    # 器，所以这就是「这个房间换机器」。The native session host and its files stay
    # in place; this is not host-loss recovery.
    async with client.test_factory() as db:
        row = await AgentSessionService(db).ensure(
            topic, "ada", harness=harness_for(None)
        )
        before_resource = row.work_lease["resource_id"]
    moved = client.put(
        f"/topics/{topic}/compute-profile",
        headers=session_auth_headers("alice"),
        json={
            "choice": {
                "name": "Second hands",
                "profile": "device",
                "device_id": client.session_test_devices["hands-b"],
            }
        },
    )
    assert moved.status_code == 200, moved.text
    central._hub.call_executor.return_value = {
        **INSTALLED,
        "pid": 123,
        "capabilities": ["prepare"],
        "context_tree": {"generation": "fixture", "entries": {}},
    }
    # 下一轮：续接指针是从这条会话行上读出来的，和 `chat.py` 读的是同一处。
    async with client.test_factory() as db:
        resumes_by = await AgentSessionService(db).resume_token(
            topic, "ada", harness=harness_for(None)
        )
    target = _open(central, project, topic, "ada", resume=resumes_by)
    assert target["device_id"] == client.session_test_devices["hands-b"]
    assert target["resource_id"] != before_resource

    opened = central._ensure_screen.await_args.kwargs
    assert opened["device_id"] == "center", opened
    assert opened["launch"].resume_session_id == "conversation-1", opened

    # Separately verify the recorded host is respected. Updating placement is
    # only metadata here: no claim is made that transcripts reached that host.
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        before = await sessions.place(topic, "ada", harness=harness_for(None))
        await sessions.remember_place(
            topic_id=topic,
            agent_handle="ada",
            work_lease=before.lease,
            runtime_location={
                "device_id": "center-two",
                "resource_id": before.resource_id,
                "channel": before.channel,
            },
            harness=harness_for(None),
        )
        await db.commit()
        resumes_by = await sessions.resume_token(
            topic, "ada", harness=harness_for(None)
        )
    on_new_host = _open(central, project, topic, "ada", resume=resumes_by)

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
            harness=harness_for(None),
        )
        await db.commit()
        assert await sessions.has_run(topic) is False
        await sessions.remember(
            topic_id=topic,
            agent_handle="ada",
            resume_token="conversation-1",
            harness=harness_for(None),
        )
        await db.commit()
        assert await sessions.has_run(topic) is True


@pytest.mark.anyio
async def test_a_room_that_switched_harness_is_claimed_by_one_channel(
    client, room, monkeypatch
):
    """换过骨架的房间，冷启动时每个骨架只认领自己那行——别的骨架的一概不认。

    会话行按 (房间, agent, 骨架) 各占一行，而换骨架的时候没有任何地方去把旧那行
    的位置清空，所以这样的房间带着两行非空的 ``runtime_location``。收养清单把同
    一座位的两行都交出来（多 agent 的房间每个座位都要被接回来），防认错靠两层：
    屏只有一块，Claude Code 的驱动按 (房间, 机器) 去重后只认回一次；
    会话归谁由各骨架按自己 harness 的行认，旧骨架那行的 runner 早已随换骨架被
    关掉，ping 不应答（这里的桩不报 alive），认不回来。

    认领的判据在房间的会话那一侧，是它自己的骨架——一条 ``CentralChannel``
    同时为几个骨架放会话（``build_compute_pool``），通道只答「放在我这儿的有哪
    些」（``CentralChannel.placed``），哪条会话归谁由各骨架按会话行自己认。
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
    # 收养清单按座位出：同一座位换过骨架的两行都在，新落的在前。
    assert [(row[1], row[3]) for row in placed] == [
        (topic, "pi"),
        (topic, "claude-code"),
    ]

    central = channel(client, monkeypatch)
    central.restore_screens = AsyncMock()

    # 这块屏是 pi 开的，所以 Claude Code 那一侧一条都不认领。
    runtime = sessions(central)
    assert client.portal.call(runtime.recover) == []
    central.restore_screens.assert_awaited_once_with([(project, topic, "center")])
