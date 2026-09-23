"""地点解析的入口是会话，不是房间。

两条验收（PLAN P18）：

* 同一条会话上的所有轮次拿到同一个租约句柄，同一个房间里的两条会话拿到各自的——
  后者在改动之前会撞上「执行机器与本房间已经记录的位置不一致」直接报错，因为位置
  记在房间上，一个房间只有一条。
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
from app.domain.agent.harness import SessionRef, deployment_harness
from app.domain.agent.harness.channel import Placement
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
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


async def _open(central, project, topic, agent, executor, *, resume=None):
    """One turn of one agent's session, through the resolution entry."""
    await central.ensure_ready(
        session=SessionRef(project, topic, agent, harness="claude-code"),
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System", resume_session_id=resume),
        precheck=Placement(executor, 1, agent, rented=True),
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
        ada = await sessions.place(topic, "ada", harness=deployment_harness())
        linus = await sessions.place(topic, "linus", harness=deployment_harness())
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
        again = await AgentSessionService(db).place(
            topic, "ada", harness=deployment_harness()
        )
    assert again is not None
    assert again.lease["device_id"] == "hands-a"
    assert again.resource_id == ada.resource_id


@pytest.mark.anyio
async def test_a_session_that_moves_machine_keeps_what_it_said(
    client, room, monkeypatch
):
    """搬一台会话机，下一轮在新机器上仍然接着上一轮自己说过的话。

    ``--resume`` 只在冷启动时用得上，而搬机器正是冷启动：所以「引用得上上一轮」
    的全部证据，就是送到新机器的那份启动计划带着上一轮的续接指针。
    """
    project, topic = room
    central = channel(client, monkeypatch, executors=("hands-a",))

    await _open(central, project, topic, "ada", "hands-a")
    # 骨架交回一个可续的 token——写侧和真正跑完一轮时走的是同一个入口。
    async with client.test_factory() as db:
        await AgentSessionService(db).remember(
            topic_id=topic,
            agent_handle="ada",
            resume_token="conversation-1",
            harness=deployment_harness(),
        )
        await db.commit()

    # 搬家：进程换一台会话机，手上那棵工作树不动。
    async with client.test_factory() as db:
        sessions = AgentSessionService(db)
        before = await sessions.place(topic, "ada", harness=deployment_harness())
        assert before is not None and before.machine == "center"
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
    await _open(central, project, topic, "ada", "hands-a", resume=resumes_by)

    opened = central._ensure_screen.await_args.kwargs
    assert opened["device_id"] == "center-two", opened
    assert opened["launch"].resume_session_id == "conversation-1", opened


@pytest.mark.anyio
async def test_a_room_with_no_resume_token_yet_has_not_run(db_factory, room):
    """租到机器还不算跑过——算力设置要到这条会话说出第一句才冻住。"""
    project, topic = room
    del project
    async with db_factory() as db:
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
    ``CentralChannel`` 同时被 Claude Code 和 Codex 两个 runtime 包着
    （``build_compute_pool``）。所以通道把落在自己这儿的会话原样交出来，骨架的名
    字当 ``running`` 一起交（``Channel.discover`` 的契约）。
    """
    project, topic = room
    del project
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
    central.restore_screens = AsyncMock(
        side_effect=lambda scopes: [(p, t, None, None) for p, t, _ in scopes]
    )
    central.executor.discover = AsyncMock(return_value=[])

    assert [found[3] for found in await central.discover()] == ["pi"]
    # 这块屏是 pi 开的，所以 Claude Code 那一侧一条都不认领。
    assert await ClaudeCodeRuntime(central).recover() == []
