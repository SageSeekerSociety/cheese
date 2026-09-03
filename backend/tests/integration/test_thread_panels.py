"""打开一条支线，右边那几格面板要答得出来。

工作区把一个地点的 id 交给 `/blocks`、`/doc`、`/transcript`、`/usage` 这一组路由，
一格一条。前两条早就认支线了，后两条还只认房间——于是点开一条支线，「现场」和
「用量」两格拿着一个合法 id 收到 404，而旁边两格好好的。

用量那一格还多一层：房间和支线问的**不是同一个问题**。一笔花费落库时带着地点的
两半（`topic_id` = 房间，`task_id` = 支线），所以按房间聚合天然含它派出去的支线
（房间要的就是总账）；而「这件活花了多少」只有支线那半键答得了。两个口径写在相邻
两行、长得几乎一样，最容易在重构里被写反，所以这里正着反着各钉一次。
"""

import uuid

import pytest

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.usage.repositories import UsageRepository


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _thread(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


async def _spend(factory, project_id: str, place_id: str, tokens: int) -> None:
    """一笔花费，记在这个**地点**上——`add` 自己把它拆成房间 + 支线两半。"""
    async with factory() as session:
        await UsageRepository(session).add(
            project_id=uuid.UUID(project_id),
            topic_id=uuid.UUID(place_id),
            model="m",
            input_tokens=tokens,
            output_tokens=0,
            cost_usd=0.0,
            turn_id=uuid.uuid4(),
        )
        await session.commit()


async def _event(factory, project_id: str, place_id: str, text: str) -> None:
    """一条 event —— 「现场」那一格看的就是这个（工具动作，不是消息）。"""
    async with factory() as session:
        await BlockRepository(session).add(
            project_id=uuid.UUID(project_id),
            topic_id=uuid.UUID(place_id),
            author="cheese",
            author_type=AuthorType.ai,
            content=text,
            kind=BlockKind.event,
        )
        await session.commit()


# --- 用量 ------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_room_totals_the_work_it_dispatched(client):
    """房间要的是总账：自己主线花的，加上它派出去的每一条支线花的。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    await _spend(client.test_factory, pid, room, tokens=100)
    await _spend(client.test_factory, pid, thread, tokens=42)

    stats = client.get(f"/topics/{room}/usage").json()["data"]
    assert stats["total_tokens"] == 142


@pytest.mark.anyio
async def test_a_thread_reports_only_what_that_piece_of_work_cost(client):
    """反过来的那一半：支线只答自己，不把它所在房间的账算进来。

    这条 404 过——「这件活花了多少」是人会问的问题，而房间的总账答不了它。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    await _spend(client.test_factory, pid, room, tokens=100)
    await _spend(client.test_factory, pid, thread, tokens=42)

    r = client.get(f"/topics/{thread}/usage")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total_tokens"] == 42


@pytest.mark.anyio
async def test_two_threads_in_one_room_do_not_pool_their_spend(client):
    """同一个房间里的两条支线各算各的——否则「支线单看」等于换个写法的总账。"""
    pid, room = _room(client)
    mine = _thread(client, room, title="我这条")
    other = _thread(client, room, title="别人那条")
    await _spend(client.test_factory, pid, mine, tokens=7)
    await _spend(client.test_factory, pid, other, tokens=900)

    assert client.get(f"/topics/{mine}/usage").json()["data"]["total_tokens"] == 7
    assert client.get(f"/topics/{room}/usage").json()["data"]["total_tokens"] == 907


# --- 施工现场 --------------------------------------------------------------


@pytest.mark.anyio
async def test_a_threads_site_is_its_own(client):
    """「现场」按地点分，和它旁边的对话一样：干活的是这条支线，工具是它跑的。

    答成房间的话，一条支线的现场里会混进同房间其它支线的动作——比 404 难发现。
    """
    pid, room = _room(client)
    thread = _thread(client, room)
    await _event(client.test_factory, pid, room, "房间里跑的")
    await _event(client.test_factory, pid, thread, "这条支线跑的")

    r = client.get(f"/topics/{thread}/transcript")
    assert r.status_code == 200, r.text
    assert [b["content"] for b in r.json()["data"]["data"]] == ["这条支线跑的"]


@pytest.mark.anyio
async def test_a_rooms_site_does_not_swallow_its_threads(client):
    """另一半：房间的现场还是房间自己的主线，不因为派了活就多出别处的动作。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    await _event(client.test_factory, pid, room, "房间里跑的")
    await _event(client.test_factory, pid, thread, "这条支线跑的")

    shown = client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    assert [b["content"] for b in shown] == ["房间里跑的"]


@pytest.mark.anyio
async def test_a_threads_site_pages_within_the_thread(client):
    """翻页也得留在这条支线里：游标是按 (房间, 支线) 认的，不然翻着翻着就翻到
    别人的现场去了。"""
    pid, room = _room(client)
    thread = _thread(client, room)
    for i in range(3):
        await _event(client.test_factory, pid, thread, f"第 {i} 步")
    await _event(client.test_factory, pid, room, "房间里跑的")

    first = client.get(f"/topics/{thread}/transcript?limit=2").json()["data"]
    assert [b["content"] for b in first["data"]] == ["第 1 步", "第 2 步"]
    assert first["has_more"] is True

    older = client.get(
        f"/topics/{thread}/transcript?limit=2&before={first['oldest_id']}"
    ).json()["data"]
    assert [b["content"] for b in older["data"]] == ["第 0 步"]
    assert older["has_more"] is False
