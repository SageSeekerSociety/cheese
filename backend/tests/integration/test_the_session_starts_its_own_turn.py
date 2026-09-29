"""会话自己开的一轮 —— 平台没喂它任何东西，它照样干了一整轮活。

分身跑完会给它的会话发一条完成通知，会话据此醒过来接着干。那一轮谁也没有请求：
没有提示词，没有人在等，所以它过去连一行 `agent_turns` 都没有 —— 于是它是唯一一
种任何收尸都看不见的轮次，而它的 result 落完消息就什么也不做了：不入账，不出变更
摘要。

这些测试钉的就是「它现在也是一轮」，以及它**不是**什么：只有会话在产出才开一行，
一条来路不明的分身完成通知开不了。

署名是这个房间的席位 handle：退场的是那个谁也没写过的作者值（结论 13），不是这条
记录 —— 一个 worker 做完唤醒主线程，那是同一个 handle 两条线程之间的一条便条。
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.usage.models import ResourceUsage
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import chat_ws_url, post_project, room_agent_seat


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间", "created_by": "u"},
    ).json()["data"]
    return project["id"], topic["id"]


def _one_ordinary_turn(client, topic_id: str) -> None:
    """一轮普通的、平台喂进去的轮次 —— 之后这个房间才有一块活着的屏幕。"""
    with client.websocket_connect(chat_ws_url(topic_id, "u")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 你好"})
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
    """轮询直到 `fn()` 给出真值：记录落进房间是读者那条循环的事，测试只能等它。"""
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
    # 一条便条有发件人：署的是这个房间的席位 handle，不是某个谁也没写过的作者值。
    assert row.author == room_agent_seat(client, room_id)
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
    assert closed, "会话的 result 没能关掉它自己开的那一轮"


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
    """会话停下之后还会飘来来路不明的分身任务记录（没人绑定过的 task_id、
    内容是提示词碎片）。它们不是「会话在产出」，后面也不会跟一个 result —— 为它们开
    一行，就是开一行永远关不掉的轮次。"""
    _project_id, room_id = _room(client)
    _one_ordinary_turn(client, room_id)
    before = {row.id for row in _turns(client, room_id)}

    stub_hooks.record(
        uuid.UUID(room_id),
        type="system",
        subtype="task_notification",
        task_id="nobody-bound-this",
        tool_use_id="toolu_stray",
        status="completed",
        summary="你是芝士。",
    )
    stub_hooks.record(
        uuid.UUID(room_id),
        type="system",
        subtype="task_started",
        task_id="nobody-bound-this",
        tool_use_id="toolu_stray",
        task_type="local_agent",
        prompt="你是芝士。",
    )
    # 同样要把那条循环叫醒几次，否则「什么都没发生」只是因为它根本没醒过。
    import time

    for _ in range(20):
        client.get(f"/topics/{room_id}")
        time.sleep(0.02)
    assert {row.id for row in _turns(client, room_id)} == before


def test_a_teammates_own_turn_stays_the_teammates(client, stub_hooks):
    """一位不是项目默认的队友，它的会话自己开了一轮：那一轮是它的，不是默认队友的。
    这时有人点它的名插一句话，话当场进它正在跑的会话，不用排队。"""
    from app.domain.agent.platform_notices import EVENT_DELIVERY_FALLBACK
    from app.domain.block.models import Block
    from app.domain.identity.handles import agent_instance_handle
    from tests.integration.conftest import session_auth_headers

    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    reviewer = client.post(
        f"/projects/{project}/agents",
        json={"handle": "reviewer", "display_name": "审稿人"},
    ).json()["data"]
    room_id = client.post(
        "/topics",
        json={"project_id": project, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    seat = agent_instance_handle(reviewer["id"])
    seated = client.post(
        f"/topics/{room_id}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text

    def say(content: str) -> None:
        with client.websocket_connect(chat_ws_url(room_id, "alice")) as ws:
            ws.send_json({"type": "message", "content": content})
            while ws.receive_json()["type"] not in ("done", "error"):
                pass
        _wait_work_idle()

    say("@审稿人 看一下")
    before = {row.id for row in _turns(client, room_id)}

    stub_hooks.uses(uuid.UUID(room_id), "Bash", agent=seat, command="ls")
    assert _wait_for(
        client,
        room_id,
        lambda: [r for r in _turns(client, room_id) if r.id not in before],
    ), "审稿人的会话自己干起活来，这一轮却没开出来"

    say("@审稿人 顺便看看这个")

    async def _queued() -> list[str]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Block).where(Block.topic_id == uuid.UUID(room_id))
            )
            return [
                block.content or ""
                for block in rows
                if (block.meta or {}).get("event_type") == EVENT_DELIVERY_FALLBACK
            ]

    assert asyncio.run(_queued()) == [], "点名它的话没进它正在跑的会话，排了队"
    stub_hooks.stops(uuid.UUID(room_id), "看完了", agent=seat)


def test_a_turn_the_platform_starts_runs_as_the_teammate_it_names(client, stub_hooks):
    """平台自己起的一轮（文档提醒、检查变红）点的是一个席位。房间里坐着的只有一位
    不是项目默认的队友时，这一轮是那位队友的会话，不会给项目默认的芝士另开一条。"""
    from app.api.deps import get_chat_service
    from app.domain.agent_session.models import AgentSession
    from app.domain.identity.handles import agent_instance_handle
    from app.domain.topic.doc_nudge import runner_submit
    from tests.integration.conftest import session_auth_headers

    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    reviewer = client.post(
        f"/projects/{project}/agents",
        json={"handle": "reviewer", "display_name": "审稿人"},
    ).json()["data"]
    room_id = client.post(
        "/topics",
        json={"project_id": project, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    default_seat = room_agent_seat(client, room_id)
    seat = agent_instance_handle(reviewer["id"])
    headers = session_auth_headers("alice")
    seated = client.post(
        f"/topics/{room_id}/members",
        json={"handle": seat, "role": "member", "actor": "alice"},
        headers=headers,
    )
    assert seated.status_code == 200, seated.text
    left = client.delete(f"/topics/{room_id}/members/{default_seat}", headers=headers)
    assert left.status_code == 200, left.text
    assert room_agent_seat(client, room_id) == seat

    chat = client.app.dependency_overrides[get_chat_service]()

    async def remind() -> None:
        runner_submit(chat)(uuid.UUID(room_id), seat, "把文档补上", "提醒补文档", {})

    client.portal.call(remind)
    assert _wait_for(client, room_id, lambda: _turns(client, room_id)), "这一轮没起来"
    _wait_work_idle()

    async def _sessions() -> set[str]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(AgentSession.agent_handle).where(
                    AgentSession.topic_id == uuid.UUID(room_id)
                )
            )
            return set(rows)

    assert asyncio.run(_sessions()) == {reviewer["handle"]}
