"""不碰文件、不跑命令的一轮不去租手（PLAN P21，结论 19，不变量 I1/I2）。

会话先于地点：一轮的顺序是「解析被点名的参与者 → 取会话 → 问 needs_place →
需要才去租一双手」。一间私聊桌上只有对话、记忆和平台工具，所以项目那台工作机
在不在线与它无关。

两条验收：

* **I2** 所有工作机离线时，私聊里发一句仍然有回复；
* **I1** 清空 device / device_topic / project_machines / device_health 四张表之后，
  名册、时间线与 ``GET /awaiting-me`` 照常。

不租手就是不占机器，所以私聊在 ``device_topic`` 上不该有行：写这行的那条分支已经
删了，存量由迁移 ``a7f1c0d4e2b9`` 收干净，最后一条测的就是它那段 SQL。
"""

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
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
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.claude_code.session_launch import ClaudeLaunch
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.device.models import DeviceRow, DeviceTopicRow
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.project.services import ProjectService
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic.services import TopicService
from app.domain.user.models import User
from tests.conftest import StubChannel, finish_turn, settle_turn
from tests.integration.conftest import post_project, registered, session_auth_headers

pytestmark = pytest.mark.anyio

INSTALLED = {
    "workspace": "/project",
    "mcp_servers": [],
    "state": "/room/.cheese/executor",
}


@pytest.fixture
def room(client):
    project = post_project(
        client, json={"name": "No hands", "owner_handle": "alice"}
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
    executor = DeviceChannel(hub=hub, session_factory=client.test_request_factory)
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


async def test_a_channel_nobody_wraps_answers_the_question_too(
    business_db_factory, room, monkeypatch
):
    """pi 不被 ``CentralChannel`` 包着，所以这一问它自己也要答得出来。

    项目一台在线工作机也没有：要手的一轮该被挡下来，不要手的那一轮该落在这条会话
    自己的机器上，而不是回头去要一台项目机器（不变量 I2）。
    """
    project, topic = room
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    hub: Any = SimpleNamespace(is_online=lambda device: device == "center")
    channel = DeviceChannel(hub=hub, session_factory=business_db_factory)
    session = SessionRef(project, topic, "cheese", harness="pi")

    resolved = await channel.precheck(session, needs_place=False)

    assert resolved.rented is False
    assert resolved.machine == "center"
    assert resolved.agent_user_id and looks_like_agent_handle(resolved.agent_handle)

    with pytest.raises(ScreenSetupError):
        await channel.precheck(session, needs_place=True)


async def test_a_session_with_no_hands_runs_in_its_own_scratch_area(
    client, room, monkeypatch
):
    """没租手的一轮跑在会话自己的草稿区里，而不是一台工作机上（结论 19）。"""
    project, topic = room
    central = central_over_offline_hands(client, monkeypatch)

    client.portal.call(
        lambda: central.ensure_ready(
            session=SessionRef(project, topic, "cheese", harness="claude-code"),
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={},
            launch=ClaudeLaunch("System"),
            precheck=Placement("center", 1, "cheese-x", rented=False),
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
        await central.ensure_ready(
            session=ref,
            token=mint_scoped_token(project_id=str(project), topic_id=str(topic)),
            env={},
            launch=ClaudeLaunch("System"),
            precheck=await central.precheck(ref, needs_place=True),
        )

    client.portal.call(open_room)
    opened = central._ensure_screen.await_args.kwargs
    target = json.loads(opened["env"]["CHEESE_EXECUTION_TARGET"])
    assert opened["device_id"] == "center"
    assert target["kind"] == "deferred"
    central._hub.exec.assert_not_awaited()


async def test_the_hands_decide_the_workspace_not_the_memory_scope(
    business_db_factory, room, monkeypatch
):
    """开在草稿区还是项目工作区，由「租到手没有」决定；记忆算谁的只管记忆。

    今天「不租手」与「记忆算个人的」恰好是同一个比特，所以拿后者挑工作区还看不
    出问题。第二种不租手的轮次一出现（平台自己起的那几轮就是），拿记忆范围去挑
    的那条路就会在会话机上打开项目工作区——一个事实只该声明一次。
    """
    project, topic = room
    hub: Any = SimpleNamespace(is_online=lambda device: True)
    channel = DeviceChannel(hub=hub, session_factory=business_db_factory)
    channel._existing_screen = lambda *args: None
    channel._ensure_screen = AsyncMock(return_value=SimpleNamespace(device_id="center"))
    session = SessionRef(project, topic, "cheese", harness="pi")
    token = mint_scoped_token(project_id=str(project), topic_id=str(topic))

    await channel.ensure_ready(
        session=session,
        token=token,
        env={},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=Placement("center", 1, "cheese-x", rented=False),
    )

    opened = channel._ensure_screen.await_args.kwargs
    target = json.loads(opened["env"]["CHEESE_EXECUTION_TARGET"])
    assert target["kind"] == "private"
    assert target["device_id"] == "center"

    # 反过来也要立得住：租到手的一轮，记忆算个人的也照样开在项目工作区里。
    channel._ensure_screen.reset_mock()
    await channel.ensure_ready(
        session=session,
        token=token,
        env={},
        memory_scope="personal",
        owner="u",
        turn_id=None,
        launch=None,
        precheck=Placement("worker", 1, "cheese-x", rented=True),
    )

    opened = channel._ensure_screen.await_args.kwargs
    assert "CHEESE_EXECUTION_TARGET" not in opened["env"]
    assert opened["env"]["CHEESE_MEMORY_SCOPE"] == "personal"


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

    async def ensure(self, session, opening):
        # The turn says whether it needs hands (`Opening.needs_place`); a
        # channel with no machine to give refuses only the turn that does.
        self.asked.append(opening.needs_place)
        if opening.needs_place:
            raise ScreenSetupError("没有在线的绑定设备可运行本轮")
        return await super().ensure(session, opening)


async def test_a_private_chat_answers_while_every_work_machine_is_offline(
    business_db_factory, tmp_path
):
    """I2：工作机全部离线，私聊里问一句「刚才那个结论是什么」，照样有回复。"""
    factory = business_db_factory
    channel = HandsRefused()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([channel.runtime], channel.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    channel.reply = "上一轮的结论是先把闸门做出来。"
    async with factory() as session:
        await registered(session, "u")
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
    await finish_turn(svc, topic_id)

    assert channel.asked == [False], "私聊这一轮不该去要手"
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    assert any(
        looks_like_agent_handle(b.author)
        and b.kind == BlockKind.event
        and b.content == channel.reply
        for b in blocks
    ), [(b.author, b.kind, b.content) for b in blocks]


async def test_a_room_turn_still_waits_for_its_hands(business_db_factory, tmp_path):
    """反面：房间里的一轮照样要手，要不到就说出来——不是所有轮次都放行。"""
    factory = business_db_factory
    channel = HandsRefused()
    svc = ChatService(
        session_factory=factory,
        compute=ComputePool([channel.runtime], channel.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with factory() as session:
        await registered(session, "u")
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


def _release_private_room_pins():
    """迁移 a7f1c0d4e2b9 里的那段 SQL 本人——照抄一份就测不到要发布的东西了。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "a7f1c0d4e2b9_private_rooms_hold_no_machine.py"
    )
    spec = importlib.util.spec_from_file_location("_private_room_pins", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.release_private_room_pins


async def test_a_private_room_lets_go_of_the_machine_it_no_longer_holds(
    business_db_factory, room
):
    """私聊不占机器（结论 19），所以 ``device_topic`` 上不该有它的行。

    删掉写这一行的那条分支只挡住「以后不再写」；库里已经写下的旧行没有人再维护，
    而读它的人还在，读的时候也不问这一轮租没租手——``judge_host_failure`` 会把一
    轮根本没用过的机器判成连续失败并在房间里点它的名，归档清理会去要一台什么都
    不放的机器交出目录。存量由迁移收干净，房间自己的绑定一行不动。
    """
    project, work_room = room
    private_room = uuid.uuid4()

    async with business_db_factory() as session:
        owner = User(
            username="pin-owner",
            email="pin-owner@example.io",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(owner)
        await session.flush()
        session.add(
            Topic(
                id=private_room,
                project_id=project,
                title="芝士",
                kind=TopicKind.topic,
                is_private=True,
            )
        )
        session.add(
            DeviceRow(
                device_id="moved-away",
                name="moved-away",
                token="tok-moved-away",
                owner_user_id=owner.id,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add_all(
            [
                DeviceTopicRow(topic_id=private_room, device_id="moved-away"),
                DeviceTopicRow(topic_id=work_room, device_id="moved-away"),
            ]
        )
        await session.commit()

    release = _release_private_room_pins()

    async def _run() -> dict:
        async with business_db_factory() as session:
            report = await session.run_sync(lambda conn: release(conn))
            await session.commit()
            return report

    report = await _run()
    assert report == {"before": 1, "deleted": 1, "after": 0}

    async with business_db_factory() as session:
        pinned = set(
            (await session.execute(sql("SELECT topic_id FROM device_topic")))
            .scalars()
            .all()
        )
    assert private_room not in pinned
    assert work_room in pinned

    # 幂等：再跑一遍什么都不匹配。
    assert await _run() == {"before": 0, "deleted": 0, "after": 0}
