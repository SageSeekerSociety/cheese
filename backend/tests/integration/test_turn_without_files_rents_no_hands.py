"""不碰文件、不跑命令的一轮不去租手（PLAN P21，结论 19，不变量 I1/I2）。

会话先于地点：一轮的顺序是「解析被点名的参与者 → 取会话 → 问 needs_place →
需要才去租一双手」。改动之前 ``CentralChannel.precheck`` 确认中心会话机在线之后
**无条件**去问执行机，于是一间私聊——它桌上只有对话、记忆和平台工具——也会因为
项目那台工作机离线而整轮开不起来。

两条验收：

* **I2** 所有工作机离线时，私聊里发一句仍然有回复；
* **I1** 清空 device / device_topic / project_machines / device_health 四张表之后，
  名册、时间线与 ``GET /awaiting-me`` 照常。
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text as sql

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import ScreenSetupError
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import session_auth_headers

pytestmark = pytest.mark.anyio

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
def room(client):
    project = client.post(
        "/projects", json={"name": "No hands", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work", "created_by": "alice"},
    ).json()["data"]
    return uuid.UUID(project["id"]), uuid.UUID(topic["id"])


def central_over_offline_hands(client, monkeypatch):
    """中心会话机在线，而每一台工作机都不在——I2 说的就是这个局面。"""
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub: Any = SimpleNamespace(
        is_online=lambda device: device == "center",
        call_executor=AsyncMock(return_value={"generation": "fixture", "entries": {}}),
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INSTALLED)}),
    )
    executor = DeviceChannel(hub=hub, session_factory=client.test_factory)
    executor.precheck = AsyncMock(
        side_effect=ScreenSetupError("没有在线的绑定设备可运行本轮")
    )
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    central._wait_executor = AsyncMock()
    return central


async def test_a_turn_that_needs_no_place_never_asks_for_hands(
    client, room, monkeypatch
):
    """needs_place 是真的分支：假的时候执行机连问都不问。"""
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)
    session = SessionRef(project, topic, "cheese", "claude-code")

    resolved = await central.precheck(session, needs_place=False)

    central.executor.precheck.assert_not_awaited()
    machine, agent_user_id, agent_handle = resolved
    # 没有执行机的那一位用 None 说出来；身份照样解析得到，它本来就不在执行机上。
    assert machine is None
    assert agent_user_id and looks_like_agent_handle(agent_handle)

    # 同一条会话、同一台离线的工作机，要手的一轮照旧被挡下来。
    with pytest.raises(ScreenSetupError, match="没有在线的绑定设备"):
        await central.precheck(session, needs_place=True)


async def test_a_session_with_no_hands_runs_in_its_own_scratch_area(
    client, room, monkeypatch
):
    """没租手的一轮跑在会话自己的草稿区里，而不是一台工作机上（结论 19）。"""
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)

    await central.ensure_ready(
        session=SessionRef(project, topic, "cheese", "claude-code"),
        token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
        env={},
        launch=ClaudeLaunch("System"),
        precheck=(None, 1, "cheese-x"),
    )

    opened = central._ensure_screen.await_args.kwargs
    target = json.loads(opened["env"]["CHEESE_EXECUTION_TARGET"])
    assert opened["device_id"] == "center"
    assert target["kind"] == "private"
    assert target["device_id"] == "center"
    # 装执行器要在工作机上跑一段脚本。一轮不租手，那段脚本就一次也不该跑。
    central._hub.exec.assert_not_awaited()


class HandsRefused(StubChannel):
    """一条只有在这一轮要手的时候才拿得出机器的通道。"""

    name = "hands-refused"

    def __init__(self):
        super().__init__()
        self.asked: list[bool] = []

    async def precheck(self, session, *, needs_place=True):
        self.asked.append(needs_place)
        if needs_place:
            raise ScreenSetupError("没有在线的绑定设备可运行本轮")
        return None


async def test_a_private_chat_answers_while_every_work_machine_is_offline(
    client, tmp_path
):
    """I2：工作机全部离线，私聊里问一句「刚才那个结论是什么」，照样有回复。"""
    factory = client.test_factory
    channel = HandsRefused()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([channel.runtime], channel.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    channel.reply = "上一轮的结论是先把闸门做出来。"
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).get_or_create_private(
            project_id=project.id, user_handle="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="刚才那个结论是什么", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    assert channel.asked == [False], "私聊这一轮不该去要手"
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert any(
        looks_like_agent_handle(b.author)
        and b.kind == BlockKind.event
        and b.content == channel.reply
        for b in blocks
    ), [(b.author, b.kind, b.content) for b in blocks]


async def test_a_room_turn_still_waits_for_its_hands(client, tmp_path):
    """反面：房间里的一轮照样要手，要不到就说出来——不是所有轮次都放行。"""
    factory = client.test_factory
    channel = HandsRefused()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([channel.runtime], channel.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="u"
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="把 README 改一下", summon=True
    ):
        pass
    await settle_turn(svc, topic_id)

    assert channel.asked == [True]
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert any("没有在线的绑定设备" in (b.content or "") for b in blocks), [
        b.content for b in blocks
    ]


async def test_the_platform_still_works_with_no_machines_at_all(client, room):
    """I1：agent 的存在不依赖任何一台机器。

    清空四张机器表，名册、时间线和 ``GET /awaiting-me`` 一样照常。
    """
    project, topic = room
    alice = session_auth_headers("alice")
    # 一道等人回答的题：它同时走时间线、通知投递和 `/awaiting-me` 三条路。
    said = client.post(
        f"/topics/{topic}/ask",
        json={"question": "先记一句", "options": ["记", "不记"]},
        headers=alice,
    )
    assert said.status_code == 200, said.text

    async with client.test_factory() as session:
        for table in ("device_topic", "device_health", "project_machines", "device"):
            await session.execute(sql(f"DELETE FROM {table}"))
        await session.commit()

    members = client.get(f"/topics/{topic}/members", headers=alice)
    timeline = client.get(f"/topics/{topic}/blocks", headers=alice)
    awaiting = client.get("/awaiting-me", headers=alice)
    overview = client.get(f"/projects/{project}", headers=alice)

    assert members.status_code == 200, members.text
    assert timeline.status_code == 200, timeline.text
    assert any(b["content"] == "先记一句" for b in timeline.json()["data"]["data"]), (
        timeline.text
    )
    assert awaiting.status_code == 200, awaiting.text
    assert overview.status_code == 200, overview.text
