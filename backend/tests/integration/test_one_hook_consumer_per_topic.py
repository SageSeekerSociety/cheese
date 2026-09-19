"""一个话题，一条消费循环——不管这个部署有几台机器。

钩子从机器回来时走的是一条进程级的队列：``hook_router`` 按话题 id 发一个 sink，
读它的是运行时的 ``_consume_subscription``。这条队列**只能有一个读者**。两个读者
不是「各读一份」，是**瓜分**：一句被切成四片的话，一个读者拿到 0、2 片，另一个拿到
1、3 片，各自的 assembler 凑出半句就落库；收尾的 Stop 带着全文再来一遍，而「这句话
已经说过了」那份名单是每个读者自己的，彼此看不见——于是一句话在房间里出现三条。

它是怎么变成两个读者的：``recover`` 让每条通道去认领「还活着的屏幕」，而
``DeviceChannel.discover`` 列的是「所有在线连接器上绑定的话题」，不看这台机器归不归
自己管；``CloudChannel`` 原样继承了它。于是每次后端重启或连接器重连，device 和 cloud
两个运行时会发现同一批话题，各自订阅、各起一个消费循环——而 ``ensure_subscription``
的幂等只查自己实例私有的那张表，拦不住对方。

所以这里测的是行为，不是实现：一台云机器上的话题，钩子回来时房间里**只多出一条**
消息，且文本是完整的。下面那条分流测试是同一件事的另一头——机器的归属由
``device.supply`` 认定（平台开的云机器归 cloud 通道，人自己接进来的归 device 通道），
这是唯一一处两条通道能对同一台机器给出不同答案的地方。
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.cloud_provider import CloudChannel, CloudLease
from app.domain.agent.compute import ComputePool
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import ClaudeCodeRuntime
from app.domain.agent.harness.claude_code.hook_events import HookRouter
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService

pytestmark = pytest.mark.anyio

REPLY = "这轮我把三处都改了，测试也跑绿了。"


class _Hub:
    """连接器状态是 hub 唯一贡献的事实：哪些机器现在连着。"""

    def __init__(self, online: set[str]) -> None:
        self.online = online

    def online_device_ids(self) -> list[str]:
        return sorted(self.online)

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online

    async def list_screens(self, device_id: str) -> list[dict]:
        return []


async def _seed(factory) -> dict[str, object]:
    """一个项目、两个房间，各绑一台机器：一台平台开的云机器，一台人自己接进来的。"""
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        topics = TopicService(session)
        on_cloud = await topics.create(
            project_id=project.id, title="云上的房间", created_by="alice"
        )
        on_own_box = await topics.create(
            project_id=project.id, title="自家机器上的房间", created_by="alice"
        )
        owner = await IdentityService(session).ensure_agent_user()
        devices = sql_device_service(session)
        cloud_box = await devices.approve(
            await devices.start("cloud-box"),
            owner_user_id=owner.id,
            supply=Supply.cloud,
            visibility=Visibility.host,
        )
        own_box = await devices.approve(
            await devices.start("own-box"),
            owner_user_id=owner.id,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        await devices.bind_topic_device(
            on_cloud.id, cloud_box.device_id, visibility=Visibility.host
        )
        await devices.bind_topic_device(
            on_own_box.id, own_box.device_id, visibility=Visibility.host
        )
        seeded = {
            "project_id": project.id,
            "on_cloud": on_cloud.id,
            "on_own_box": on_own_box.id,
            "cloud_device": cloud_box.device_id,
            "own_device": own_box.device_id,
        }
        await session.commit()
    return seeded


def _channels(factory, hub: _Hub) -> tuple[DeviceChannel, CloudChannel]:
    async def _never_asked(*_args: object, **_kwargs: object) -> CloudLease | None:
        raise AssertionError("认领屏幕不需要问租约")

    device = DeviceChannel(session_factory=factory, hub=hub)
    cloud = CloudChannel(
        session_factory=factory,
        hub=hub,
        configured=True,
        ensure_topic_cloud=_never_asked,
        read_topic_cloud=_never_asked,
    )
    return device, cloud


def _flush(mid: str, idx: int, delta: str, *, final: bool = False) -> dict:
    return {
        "hook_event_name": "MessageDisplay",
        "message_id": mid,
        "index": idx,
        "final": final,
        "delta": delta,
        "_eid": f"{mid}-{idx}",
    }


async def test_each_channel_recovers_only_the_machines_it_owns(client):
    """归属由 supply 认定：平台开的云机器归 cloud，人接进来的归 device。

    两条通道共用一张绑定表（cloud 也调 ``bind_topic_device``），所以「这条绑定归谁」
    只能问机器本身。答错的后果不是少认领一个话题，是同一个话题被认领两次。
    """
    factory = client.test_factory
    seeded = await _seed(factory)
    hub = _Hub({str(seeded["cloud_device"]), str(seeded["own_device"])})
    device, cloud = _channels(factory, hub)

    on_device = {
        topic_id for _project, topic_id, _screen, _runs in await device.discover()
    }
    on_cloud = {
        topic_id for _project, topic_id, _screen, _runs in await cloud.discover()
    }

    assert on_device == {seeded["on_own_box"]}
    assert on_cloud == {seeded["on_cloud"]}


async def test_a_reconnect_does_not_hand_the_same_topic_to_both_channels(client):
    """连接器重连走的是 ``recover(device_id)``，一台机器只该惊动它自己那条通道。"""
    factory = client.test_factory
    seeded = await _seed(factory)
    hub = _Hub({str(seeded["cloud_device"]), str(seeded["own_device"])})
    device, cloud = _channels(factory, hub)

    cloud_device_id = str(seeded["cloud_device"])
    assert [t for _p, t, _s, _r in await device.discover(cloud_device_id)] == []
    assert [t for _p, t, _s, _r in await cloud.discover(cloud_device_id)] == [
        seeded["on_cloud"]
    ]


async def test_a_recovered_message_lands_once_and_whole(client, tmp_path):
    """后端重启后，云机器上那句话在房间里只出现一条，而且是完整的一句。

    两个运行时（device 与 cloud）共用进程里唯一那个 ``HookRouter``，走的是生产的
    ``ChatService.recover_sessions`` —— 也就是 ``main.py`` 启动时和连接器重连时走的
    那条路。修复前这条路让两个运行时都订阅上这个话题：四个分片被两条消费循环瓜分成
    两条半截消息，Stop 再补一条全文，房间里一共三条。
    """
    factory = client.test_factory
    seeded = await _seed(factory)
    topic_id = seeded["on_cloud"]
    assert isinstance(topic_id, uuid.UUID)
    hub = _Hub({str(seeded["cloud_device"]), str(seeded["own_device"])})
    device, cloud = _channels(factory, hub)

    router = HookRouter()
    pool = ComputePool(
        [
            ClaudeCodeRuntime(device, router=router),
            ClaudeCodeRuntime(cloud, router=router),
        ],
        device.name,
    )
    service = ChatService(
        session_factory=factory,
        compute=pool,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    await service.recover_sessions()

    key = str(topic_id)
    for index, part in enumerate(["这轮我把", "三处都改了，", "测试也", "跑绿了。"]):
        router.push(key, _flush("m1", index, part, final=index == 3))
    router.push(
        key,
        {
            "hook_event_name": "Stop",
            "last_assistant_message": REPLY,
            "session_id": "s1",
            "_eid": "stop-1",
        },
    )
    await asyncio.wait_for(router.subscribe(key).queue.join(), timeout=10)

    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    said = [
        (block.content or "").strip()
        for block in blocks
        if block.kind == BlockKind.event
        and block.author_type == AuthorType.ai
        and (block.meta or {}).get("progress")
    ]

    assert said == [REPLY]


async def test_screens_are_adopted_with_no_transaction_open(client):
    """Adopting a screen asks the connection owner (a network call when it
    runs as its own service). That happens after the database work of the
    device is committed, not inside it: one transaction across every room of
    a reconnecting device kept a pool connection for the whole device."""
    factory = client.test_factory
    seeded = await _seed(factory)
    own_device = str(seeded["own_device"])
    open_sessions = 0
    seen_open: list[int] = []

    class _Scope:
        """One session of the channel's factory, counted while it is open."""

        def __init__(self):
            self.session = factory()

        async def __aenter__(self):
            nonlocal open_sessions
            open_sessions += 1
            return await self.session.__aenter__()

        async def __aexit__(self, *exc):
            nonlocal open_sessions
            open_sessions -= 1
            return await self.session.__aexit__(*exc)

    class _AdoptingHub(_Hub):
        async def list_screens(self, device_id: str) -> list[dict]:
            return [
                {
                    "sid": "s1",
                    "screen": "tok",
                    "command": ["claude"],
                    "env": {
                        "CHEESE_PROJECT": str(seeded["project_id"]),
                        "CHEESE_TOPIC": str(seeded["on_own_box"]),
                    },
                }
            ]

        async def adopt_screen(self, device_id: str, sid: str, **kwargs):
            seen_open.append(open_sessions)
            return SimpleNamespace(
                resource_id=kwargs["resource_id"], sid=sid, device_id=device_id
            )

    device = DeviceChannel(session_factory=_Scope, hub=_AdoptingHub({own_device}))
    restored = await device.discover(own_device)
    assert [t for _p, t, _s, _r in restored] == [seeded["on_own_box"]]
    assert restored[0][2] is not None
    assert seen_open == [0], "adopt_screen ran inside an open session"
