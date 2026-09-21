"""便条的写侧和定时投递原语（结论 11、12、17；不变量 I12、I14②）。

这两样是平台 MCP 那六样里今天才建出来的两样，所以它们的验收在这里，而不是和工具表
一起放在 contract 里：一张便条到底进没进时间线、一个闹钟到点产生的是投递还是一轮被
平台点起的对话，都要一个真的房间才答得出来。
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.delivery.models import TimedDelivery
from app.domain.delivery.note import NOT_YOUR_OWN_THREAD
from app.domain.delivery.timer import DELIVERED_AS_ASKED, deliver_due


def _project(client, name: str, owner: str = "user-1") -> str:
    r = client.post("/projects", json={"name": name, "owner_handle": owner})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project_id: str, title: str, owner: str = "user-1") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": owner},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _seat(client, topic_id: str) -> str:
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    seats = [m["member_handle"] for m in rows if m["agent"]]
    assert len(seats) == 1, seats
    return seats[0]


def _blocks(client, topic_id: str) -> list:
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_a_note_reaches_the_other_thread_of_the_same_handle(client, monkeypatch):
    """同一个 handle 的另一条线程收得到，而且这张便条不进时间线。

    不进时间线是这条通道的全部意义：两条线程互相对暗号，房间里的人一句都不需要读。
    """
    project = _project(client, "同一个项目")
    here = _room(client, project, "房间一")
    there = _room(client, project, "房间二")
    assert _seat(client, here) == _seat(client, there)
    before = len(_blocks(client, there))

    handed = []

    async def remember(self, topic_id, notice, *, blocks=()):
        handed.append((str(topic_id), notice))
        return True

    monkeypatch.setattr(ChatService, "notify_running_turn", remember)

    r = client.post(
        f"/topics/{here}/note", json={"thread": there, "content": "口径改了，按新的来"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["delivered"] is True
    assert handed == [(there, "口径改了，按新的来")]
    assert len(_blocks(client, there)) == before, "便条落到时间线上去了"


def test_a_note_to_another_handle_is_refused(client, monkeypatch):
    """送给另一个 handle 被拒（I14②）。

    不同 handle 之间只走 chat，agent 对 agent 也是：没有第二条私下通道，两个 agent
    要说话就在房间里说，人看得见。
    """
    here = _room(client, _project(client, "我的项目"), "房间一")
    elsewhere = _room(client, _project(client, "别人的项目"), "别人的房间")
    assert _seat(client, here) != _seat(client, elsewhere)

    handed = []

    async def remember(self, topic_id, notice, *, blocks=()):
        handed.append(str(topic_id))
        return True

    monkeypatch.setattr(ChatService, "notify_running_turn", remember)

    r = client.post(
        f"/topics/{here}/note", json={"thread": elsewhere, "content": "偷偷说一句"}
    )

    assert r.status_code >= 400, r.text
    assert NOT_YOUR_OWN_THREAD in r.text
    assert handed == [], "被拒的便条还是送出去了"


def test_a_timed_delivery_arrives_as_a_delivery_not_a_turn_the_platform_started(
    client,
):
    """到点产生的是一条投递（结论 17、I12）。

    差别不在现象上——收件人是 agent 时那一轮确实会跑起来——而在**谁决定的**：
    `submit` 收的是寻址结果，点名的是账本上记着的那个请求者自己。删掉那一行请求，
    平台就什么也不会做。
    """
    room = _room(client, _project(client, "定时"), "房间一")
    seat = _seat(client, room)
    due = datetime.now(UTC) - timedelta(minutes=1)

    r = client.post(
        f"/topics/{room}/deliveries",
        json={"at": due.isoformat(), "content": "回来看一眼那条 PR"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["to"] == seat

    submitted = []

    class Runner:
        def submit(self, chat, topic_id, **kwargs):
            submitted.append((str(topic_id), kwargs))

    first = asyncio.run(
        deliver_due(client.test_factory, chat=object(), runner=Runner())
    )
    assert first == {"delivered": 1}

    topic_id, kwargs = submitted[0]
    assert topic_id == room
    assert kwargs["content"] == "回来看一眼那条 PR"
    assert [r.handle for r in kwargs["addressed"].recipients] == [seat]
    assert "summon" not in kwargs, "平台又自己点起了一轮"
    # 房间里读得到这一轮的缘由（结论 14）：平台说的话署平台的名，那一行写着它是
    # 谁当初请来的，原话收在 detail 里。
    assert kwargs["author"] == "system"
    assert kwargs["nudge_event"] == DELIVERED_AS_ASKED
    assert kwargs["nudge_meta"]["detail"] == "回来看一眼那条 PR"

    # 递过的那一行不再递第二遍 —— 重启、重跑、两台机器同时扫都一样。
    again = asyncio.run(
        deliver_due(client.test_factory, chat=object(), runner=Runner())
    )
    assert again == {"delivered": 0}


def test_a_scan_leaves_a_row_another_scanner_already_holds(client):
    """两个后端同时扫，同一条只递一遍。

    滚动部署里新旧两个容器会同时在跑，各自都带着这条 30 秒的扫描。这里让一条别的
    连接先握住那一行，再让扫描跑一次：它跳过去（`skip_locked`），不是等在那里，也
    不是把这一条再递一遍。锁一松，下一拍照常递出去。
    """
    room = _room(client, _project(client, "同时扫"), "房间一")
    due = datetime.now(UTC) - timedelta(minutes=1)
    r = client.post(
        f"/topics/{room}/deliveries",
        json={"at": due.isoformat(), "content": "回来看一眼那条 PR"},
    )
    assert r.status_code == 200, r.text

    submitted = []

    class Runner:
        def submit(self, chat, topic_id, **kwargs):
            submitted.append(str(topic_id))

    async def _while_another_scanner_holds_it():
        async with client.test_factory() as holder:
            await holder.scalars(
                select(TimedDelivery)
                .where(TimedDelivery.delivered_at.is_(None))
                .with_for_update()
            )
            skipped = await asyncio.wait_for(
                deliver_due(client.test_factory, chat=object(), runner=Runner()),
                timeout=20,
            )
            await holder.rollback()
        return skipped

    assert asyncio.run(_while_another_scanner_holds_it()) == {"delivered": 0}
    assert submitted == [], "同一条被递了第二遍"

    again = asyncio.run(
        deliver_due(client.test_factory, chat=object(), runner=Runner())
    )
    assert again == {"delivered": 1}
    assert submitted == [room]
