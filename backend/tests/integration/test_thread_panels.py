"""打开一张卡，右边那几格面板要答得出来。

工作区把**房间**的 id 交给 `/blocks`、`/doc`、`/transcript`、`/usage` 这一组路由，
一格一条。一张卡不在这组地址里：它不是地点，读它走的是它所在的房间
（`GET /topics/{room}/tasks/{card}`），那一份载荷里就带着它自己的时间线。

用量只有一个口径：房间的总账。每一个分身都花在房间那一个会话上，所以没有第二个
表可读——按卡分摊出来的数字是编的。
"""

import uuid

import pytest

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.usage.repositories import UsageRepository
from tests.integration.conftest import post_project, session_auth_headers


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(
        f"/topics/{room_id}/split",
        json=dict(reviewer_handle="alice", **{"title": title}),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


async def _spend(factory, project_id: str, room_id: str, tokens: int) -> None:
    """一笔花费，记在这个房间的账上 —— 只有这一本账。"""
    async with factory() as session:
        await UsageRepository(session).add(
            project_id=uuid.UUID(project_id),
            topic_id=uuid.UUID(room_id),
            model="m",
            input_tokens=tokens,
            output_tokens=0,
            cost_usd=0.0,
            turn_id=uuid.uuid4(),
        )
        await session.commit()


async def _event(
    factory,
    project_id: str,
    room_id: str,
    text: str,
    task_id: str | None = None,
    author: str = "cheese",
) -> None:
    """一条 event —— 「现场」那一格看的就是这个（工具动作，不是消息）。

    地点是房间，`task_id` 才说这一步是哪个分身干的：这正是平台按 `agent_id` 给
    事件归属时写下的那一对。`author` 是署名 —— 一个房间坐得下几个队友，`现场`
    按它一次看一个。
    """
    async with factory() as session:
        await BlockRepository(session).add(
            project_id=uuid.UUID(project_id),
            topic_id=uuid.UUID(room_id),
            task_id=uuid.UUID(task_id) if task_id else None,
            author=author,
            author_type=AuthorType.participant,
            content=text,
            kind=BlockKind.event,
        )
        await session.commit()


# --- 用量 ------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_room_totals_what_ran_in_it(client):
    """房间要的是总账：它那一个会话里花掉的全部，分身花的也在里面 —— 它们本来
    就是那个会话在花钱。"""
    pid, room = _room(client)
    _thread(client, room)
    client.portal.call(
        lambda: _spend(client.test_request_factory, pid, room, tokens=100)
    )
    client.portal.call(
        lambda: _spend(client.test_request_factory, pid, room, tokens=42)
    )

    stats = client.get(f"/topics/{room}/usage").json()["data"]
    assert stats["total_tokens"] == 142


@pytest.mark.anyio
async def test_a_card_has_no_bill_of_its_own(client):
    """「这件活花了多少」没有答案，而且不该有一个编出来的答案。

    每个分身都跑在房间那一个会话上，账单只有一张。拿卡的 id 去问用量是 404 ——
    那不是一个地点。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    client.portal.call(
        lambda: _spend(client.test_request_factory, pid, room, tokens=100)
    )

    assert client.get(f"/topics/{thread}/usage").status_code == 404


# --- 施工现场 --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_cards_site_rides_with_the_card(client):
    """一张卡干了什么，在这张卡自己那份载荷里 —— 干活的是这条活的分身。

    不给它一条 `/transcript` 地址，是因为它没有地址；它的动作和它的对话是同一条
    时间线，一起回来。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    client.portal.call(
        lambda: _event(client.test_request_factory, pid, room, "房间里跑的")
    )
    client.portal.call(
        lambda: _event(client.test_request_factory, pid, room, "这条支线跑的", thread)
    )

    assert client.get(f"/topics/{thread}/transcript").status_code == 404
    blocks = client.get(f"/topics/{room}/tasks/{thread}").json()["data"]["blocks"]
    assert "这条支线跑的" in [b["content"] for b in blocks]
    assert "房间里跑的" not in [b["content"] for b in blocks]


@pytest.mark.anyio
async def test_a_rooms_site_does_not_swallow_its_threads(client):
    """另一半：房间的现场还是房间自己的主线，不因为派了活就多出别处的动作。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    client.portal.call(
        lambda: _event(client.test_request_factory, pid, room, "房间里跑的")
    )
    client.portal.call(
        lambda: _event(client.test_request_factory, pid, room, "这条支线跑的", thread)
    )

    shown = [
        b["content"]
        for b in client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    ]
    assert "房间里跑的" in shown
    assert "这条支线跑的" not in shown
    # 派出这条活是**房间自己**做的动作，所以它在房间的现场里 —— 那条活在里面干的
    # 每一件事都不在。
    assert any("派出一条活" in c for c in shown)


@pytest.mark.anyio
async def test_a_cards_timeline_can_be_cut_to_its_newest(client):
    """一条跑久了的活能有上千块，所以读它的人要能只要最近的一段 —— 和聊天窗口
    一样是底部对齐的，`limit` 截的是最新那 N 条。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    for i in range(3):
        client.portal.call(
            lambda i=i: _event(
                client.test_request_factory, pid, room, f"第 {i} 步", thread
            )
        )
    client.portal.call(
        lambda: _event(client.test_request_factory, pid, room, "房间里跑的")
    )

    newest = client.get(f"/topics/{room}/tasks/{thread}?limit=2").json()["data"]
    assert [b["content"] for b in newest["blocks"]] == ["第 1 步", "第 2 步"]

    whole = client.get(f"/topics/{room}/tasks/{thread}").json()["data"]
    assert [b["content"] for b in whole["blocks"]] == ["第 0 步", "第 1 步", "第 2 步"]


@pytest.mark.anyio
async def test_a_rooms_site_can_be_read_one_teammate_at_a_time(client):
    """一个房间坐得下几个队友，现场要能只看其中一个 —— 而且和「全部」一样是翻页
    的：只读最近一页、往上再要一页。

    过滤必须发生在分页里面（和 `kinds` 一个位置）。先把一页滤完再交出来的实现会在
    这里露馅：`limit=2` 只给回一条（另一半是别人的），`has_more` 也是照着错的集合
    算的，调用方于是从一个个洞里翻页。
    """
    pid, room = _room(client)
    for i in range(3):
        client.portal.call(
            lambda i=i: _event(
                client.test_request_factory,
                pid,
                room,
                f"甲的 {i} 步",
                author="cheese-a1",
            )
        )
        client.portal.call(
            lambda i=i: _event(
                client.test_request_factory,
                pid,
                room,
                f"乙的 {i} 步",
                author="cheese-b2",
            )
        )

    # 最新的一窗：只要一个队友的，还是满满一页。
    mine = client.get(
        f"/topics/{room}/transcript", params={"limit": 2, "author": "cheese-a1"}
    ).json()["data"]
    assert len(mine["data"]) == 2, "只滤出别人的行，说明过滤发生在分页之后"
    assert {b["author"] for b in mine["data"]} == {"cheese-a1"}
    assert mine["has_more"] is True

    # 往上再要一页：游标 + 同一个 author。
    older = client.get(
        f"/topics/{room}/transcript",
        params={"limit": 2, "before": mine["oldest_id"], "author": "cheese-a1"},
    ).json()["data"]
    assert older["has_more"] is False
    assert {b["author"] for b in older["data"]} == {"cheese-a1"}
    # 两窗接起来正好是甲的三步，不多不少 —— 也不混进乙的。
    assert [b["content"] for b in older["data"] + mine["data"]] == [
        "甲的 0 步",
        "甲的 1 步",
        "甲的 2 步",
    ]

    # 不带 author 的「全部」照旧：两个队友的行都在，一个都没被过滤掉。
    everything = client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    assert {"cheese-a1", "cheese-b2"} <= {b["author"] for b in everything}
