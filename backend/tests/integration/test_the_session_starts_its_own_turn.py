"""会话自己开的一轮 —— 平台没喂它任何东西，它照样干了一整轮活。

分身跑完会给它的会话发一条完成通知，会话据此醒过来接着干。那一轮谁也没有请求：
没有提示词，没有人在等，所以它过去连一行 `agent_turns` 都没有 —— 于是它是唯一一
种任何收尸都看不见的轮次，而它的 Stop 落完消息就什么也不做了：不入账，不出变更
摘要。

这些测试钉的就是「它现在也是一轮」，以及它**不是**什么：只有会话在产出才开一行，
一条来路不明的分身完成通知开不了。
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.usage.models import ResourceUsage
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url


def _room(client) -> tuple[str, str]:
    project = client.post("/projects", json={"name": "P"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间", "created_by": "u"},
    ).json()["data"]
    return project["id"], topic["id"]


def _one_ordinary_turn(client, topic_id: str) -> None:
    """一轮普通的、平台喂进去的轮次 —— 之后这个房间才有一块活着的屏幕。"""
    with client.websocket_connect(chat_ws_url(topic_id, "u")) as ws:
        ws.send_json({"type": "message", "content": "你好", "summon": True})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    _wait_work_idle()


def _turns(client, topic_id: str) -> list[AgentTurn]:
    async def _run() -> list[AgentTurn]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(AgentTurn)
                .where(AgentTurn.topic_id == uuid.UUID(topic_id))
                .order_by(AgentTurn.started_at)
            )
            return list(rows)

    return asyncio.run(_run())


def _wait_for(client, topic_id: str, fn, *, tries: int = 200):
    """轮询直到 `fn()` 给出真值。

    每一轮先打一个便宜的请求过去，这不是凑数：钩子是从**测试这条线程**塞进
    TestClient 那条事件循环的队列里的，塞的时候唤不醒它（`put_nowait` 跨线程叫不
    动等在那儿的 waiter）。那条循环下一次醒来是因为有请求进来 —— 不打这一下，钩子
    就一直躺在队列里，测试等满也等不到。
    """
    import time

    for _ in range(tries):
        client.get(f"/topics/{topic_id}")
        got = fn()
        if got:
            return got
        time.sleep(0.02)
    return fn()


def test_the_session_working_on_its_own_opens_an_interval(client, stub_hooks):
    """会话在没人喂它的情况下开始产出 —— 那一刻起它就是一轮，有自己那一行。"""
    _project_id, room_id = _room(client)
    _one_ordinary_turn(client, room_id)
    before = {row.id for row in _turns(client, room_id)}

    stub_hooks.uses(uuid.UUID(room_id), "Bash", command="ls")
    rows = _wait_for(
        client,
        room_id,
        lambda: [r for r in _turns(client, room_id) if r.id not in before],
    )

    assert len(rows) == 1, "会话自己干了一整轮，表里却没有这一轮"
    row = rows[0]
    assert row.author == AgentWorkRunner.SELF_STARTED_AUTHOR
    assert row.content == ""
    assert row.resendable is False, "没有提示词可发 —— 标成可重发就是叫收尸去发空气"
    assert row.delivered_at is not None
    assert row.stopped_at is None, "还在跑"

    stub_hooks.stops(uuid.UUID(room_id), "顺手看了一眼")
    closed = _wait_for(
        client,
        room_id,
        lambda: [r for r in _turns(client, room_id) if r.id == row.id and r.stopped_at],
    )
    assert closed, "会话的 Stop 没能关掉它自己开的那一轮"


def test_a_self_started_turn_is_accounted_for_when_it_stops(client, stub_hooks):
    """它烧掉的算力要跟别的轮次一样入账 —— 一轮不记账，配额就是漏的。"""
    _project_id, room_id = _room(client)
    _one_ordinary_turn(client, room_id)
    before = {row.id for row in _turns(client, room_id)}

    stub_hooks.uses(uuid.UUID(room_id), "Bash", command="ls")
    rows = _wait_for(
        client,
        room_id,
        lambda: [r for r in _turns(client, room_id) if r.id not in before],
    )
    assert rows
    turn_id = rows[0].id
    stub_hooks.stops(uuid.UUID(room_id), "顺手看了一眼")

    async def _usage() -> list[ResourceUsage]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(ResourceUsage).where(ResourceUsage.turn_id == turn_id)
                )
            )

    metered = _wait_for(client, room_id, lambda: asyncio.run(_usage()))
    assert len(metered) == 1, "自启轮次烧的算力没有入账"


def test_a_stray_worker_report_opens_nothing(client, stub_hooks):
    """会话停下之后还会飘来来路不明的分身完成通知（不同的 agent_id、空 agent_type、
    内容是提示词碎片）。它们不是「会话在产出」，后面也不会跟一个 Stop —— 为它们开
    一行，就是开一行永远关不掉的轮次。"""
    _project_id, room_id = _room(client)
    _one_ordinary_turn(client, room_id)
    before = {row.id for row in _turns(client, room_id)}

    stub_hooks.hook(
        uuid.UUID(room_id),
        hook_event_name="SubagentStop",
        agent_id="nobody-bound-this",
        last_assistant_message="你是芝士。",
    )
    stub_hooks.hook(
        uuid.UUID(room_id),
        hook_event_name="SubagentStart",
        agent_id="nobody-bound-this",
        agent_type="general-purpose",
    )
    # 同样要把那条循环叫醒几次，否则「什么都没发生」只是因为它根本没醒过。
    import time

    for _ in range(20):
        client.get(f"/topics/{room_id}")
        time.sleep(0.02)
    assert {row.id for row in _turns(client, room_id)} == before
