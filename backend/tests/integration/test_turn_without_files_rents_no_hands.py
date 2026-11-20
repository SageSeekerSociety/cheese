"""不碰文件、不跑命令的一轮不去租手（PLAN P21，结论 19，不变量 I1/I2）。

会话先于地点：一轮的顺序是「解析被点名的参与者 → 取会话 → 问 needs_place →
需要才去租一双手」。一间私聊桌上只有对话、记忆和平台工具，所以项目那台工作机
在不在线与它无关。

两条验收：

* **I2** 所有工作机离线时，私聊里发一句仍然有回复；
* **I1** 清空 device / project_machines / device_health 这几张表之后，
  名册、时间线与 ``GET /awaiting-me`` 照常。
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text as sql

from app.api import deps as session_turn_deps
from app.core.config import settings
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.chat import ChatService
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.claude_code import ClaudeLaunch
from app.domain.agent_instance.adapters import named_teammate
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.session_record.service import read
from app.domain.topic.services import TopicService
from tests.ask_fixtures import question_row
from tests.conftest import StubChannel, finish_turn, settle_turn, stub_compute
from tests.integration.conftest import (
    post_project,
    project_service,
    registered,
    session_auth_headers,
)
from tests.integration.test_central_room_sessions import screen_for

pytestmark = pytest.mark.anyio

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
def room(client):
    project = post_project(client, json={"name": "No hands"}, owner="alice").json()[
        "data"
    ]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
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
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
    executor.precheck = AsyncMock(
        side_effect=ScreenSetupError("没有在线的绑定设备可运行本轮")
    )
    central: Any = CentralChannel(executor)
    central._device_api_base = AsyncMock(return_value="http://central-api")
    central._ensure_screen = AsyncMock(
        return_value=SimpleNamespace(device_id="center", sid="s1")
    )
    central._wait_executor = AsyncMock()
    return central


async def test_a_turn_that_needs_no_place_never_asks_for_hands(
    client, room, monkeypatch
):
    """needs_place 是真的分支：假的时候执行机连问都不问。"""
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)
    session = SessionRef(project, topic, "cheese", harness="claude-code")

    resolved = client.portal.call(lambda: central.precheck(session, needs_place=False))

    central.executor.precheck.assert_not_awaited()
    # 「这一轮租没租手」就说在这一位上，下游读它；机器是这条会话自己的那台，
    # 身份照样解析得到，它本来就不在执行机上。
    assert resolved.rented is False
    assert resolved.machine == "center"
    assert resolved.agent_user_id and looks_like_agent_handle(resolved.agent_handle)

    # The same ordinary room can receive input while work tools are unavailable.
    offline = client.portal.call(lambda: central.precheck(session, needs_place=True))
    assert offline.machine == "center"
    assert offline.rented is False
    assert offline.deferred is True
    central.executor.precheck.assert_not_awaited()


async def test_a_session_with_no_hands_runs_in_its_own_scratch_area(
    client, room, monkeypatch
):
    """没租手的一轮跑在会话自己的草稿区里，而不是一台工作机上（结论 19）。"""
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)

    client.portal.call(
        lambda: screen_for(
            central,
            SessionRef(project, topic, "cheese", harness="claude-code"),
            ClaudeLaunch("System"),
            Placement("center", 1, "cheese-x", rented=False),
        )
    )

    opened = central._ensure_screen.await_args.kwargs
    target = json.loads(opened["env"]["CHEESE_EXECUTION_TARGET"])
    assert opened["device_id"] == "center"
    assert target["kind"] == "private"
    assert target["device_id"] == "center"
    # 装执行器要在工作机上跑一段脚本。一轮不租手，那段脚本就一次也不该跑。
    central._hub.exec.assert_not_awaited()


async def test_ordinary_room_opens_without_executing_on_the_session_host(
    client, room, monkeypatch
):
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)
    ref = SessionRef(project, topic, "cheese", harness="claude-code")

    async def open_room():
        await screen_for(central, ref, ClaudeLaunch("System"))

    client.portal.call(open_room)
    opened = central._ensure_screen.await_args.kwargs
    target = json.loads(opened["env"]["CHEESE_EXECUTION_TARGET"])
    assert opened["device_id"] == "center"
    assert target["kind"] == "deferred"
    central._hub.exec.assert_not_awaited()


class CountsConnections:
    """数这条通道同时开着几条数据库连接。"""

    def __init__(self, factory):
        self._factory = factory
        self.open = 0
        self.peak = 0

    def __call__(self):
        return _Tracked(self, self._factory())


class _Tracked:
    def __init__(self, counter: CountsConnections, session):
        self._counter = counter
        self._session = session

    async def __aenter__(self):
        self._counter.open += 1
        self._counter.peak = max(self._counter.peak, self._counter.open)
        return await self._session.__aenter__()

    async def __aexit__(self, *exc):
        self._counter.open -= 1
        return await self._session.__aexit__(*exc)


async def test_session_host_check_releases_connection_without_acquiring_hands(
    business_db_factory, room, monkeypatch
):
    """要手的一轮不持着一条连接去要第二条 (#1312)。

    会话机在线是两条路共同的前提，问它要开一条连接；执行机那一步自己还要开一条。
    第一条不还就去要第二条，并发到池子大小的轮次一起开场就互相等到超时——那正是
    #1312 修过的局面，房间里的每一轮都走这条路。
    """
    project, topic = room
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    counter = CountsConnections(business_db_factory)
    hub: Any = SimpleNamespace(is_online=lambda device: True)
    executor = DeviceChannel(hub=hub, session_factory=counter)
    central: Any = CentralChannel(executor)
    held_when_asked: list[int] = []

    async def hands(session, *, needs_place):
        held_when_asked.append(counter.open)
        return Placement("worker", 1, "cheese-x", rented=True)

    executor.precheck = hands

    resolved = await central.precheck(
        SessionRef(project, topic, "cheese", harness="claude-code"),
        needs_place=True,
    )

    assert resolved.rented is False
    assert resolved.deferred is True
    assert held_when_asked == [], "Opening a conversation must not acquire hands"
    assert counter.open == 0
    assert counter.peak == 1


class HandsRefused(StubChannel):
    """一条只有在这一轮要手的时候才拿得出机器的通道。"""

    name = "hands-refused"

    def __init__(self):
        super().__init__()
        self.asked: list[bool] = []

    async def precheck(self, session, *, needs_place):
        # The turn says whether it needs hands; a channel with no machine to
        # give refuses only the turn that does.
        self.asked.append(needs_place)
        if needs_place:
            raise ScreenSetupError("没有在线的绑定设备可运行本轮")
        return await super().precheck(session, needs_place=needs_place)


async def test_a_private_chat_answers_while_every_work_machine_is_offline(
    business_db_factory, tmp_path
):
    """I2：工作机全部离线，私聊里问一句「刚才那个结论是什么」，照样有回复。"""
    factory = business_db_factory
    channel = HandsRefused()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(channel, sessions=factory),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    channel.reply = "上一轮的结论是先把闸门做出来。"
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).get_or_create_private(
            project_id=project.id,
            user_handle="u",
            named_teammate=named_teammate,
        )
        topic_id = topic.id
        await session.commit()

    async for _ in svc.converse(
        topic_id=topic_id, author="u", content="刚才那个结论是什么", summon=True
    ):
        pass
    await finish_turn(svc, topic_id)

    assert channel.asked == [False], "私聊这一轮不该去要手"
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
        kept = await read(session, topic_id, reader=None)
    assert any(
        item["record"].get("type") == "result"
        and item["record"].get("result") == channel.reply
        for item in kept.items
    ), kept.items
    assert not [b for b in blocks if looks_like_agent_handle(b.author)]


async def test_a_room_turn_still_waits_for_its_hands(business_db_factory, tmp_path):
    """反面：房间里的一轮照样要手，要不到就说出来——不是所有轮次都放行。"""
    factory = business_db_factory
    channel = HandsRefused()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        compute=stub_compute(channel, sessions=factory),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
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
    said = [f"{b.content}\n{(b.meta or {}).get('detail') or ''}" for b in blocks]
    assert any("没有在线的绑定设备" in text for text in said), said


async def test_the_platform_still_works_with_no_machines_at_all(client, room):
    """I1：agent 的存在不依赖任何一台机器。

    清空四张机器表，名册、时间线和 ``GET /awaiting-me`` 一样照常。
    """
    project, topic = room
    alice = session_auth_headers("alice")
    # 一道等人回答的题：它同时走时间线、通知投递和 `/awaiting-me` 三条路。
    # 直插一条题 —— 这里要的是「有题在等」这个状态，不是提问这条路本身。
    said = question_row(client, topic, question="先记一句", asked=["alice"])
    assert said["id"]

    async with client.test_factory() as session:
        for table in ("device_health", "project_machines", "device"):
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
