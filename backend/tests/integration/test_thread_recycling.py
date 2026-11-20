"""一条没人回来的支线：满三天收进「已回收」，以及放回来的两条路。

一条支线里报错，而没人回头修，它就一直把整间频道点红 —— `block.waits` 只认恢复、
不认时间。所以满了 `FAILURE_GIVES_UP_AFTER`（三天）之后：

- 这条支线被收起来（`thread/recycle.py`）：不再算进频道那一行的红灯，也从概览的支线
  列表里挪到「已回收」一栏；
- 频道自己那条线的红灯同样过了三天就不再亮 —— 它没有支线可收，只有这一条路；
- 里面的一切照旧：块一条不删，翻得到、点得进去读，说一句话就回到默认那一栏；
- 收起来的支线谁看得到频道谁就读得到，「恢复」只给开这条支线的人和频道管理员；
- 正跑着的支线不是死掉的，只是慢：这一趟不收它。

Depends on real HTTP, PostgreSQL and the room's runner; the session is the
scripted stub.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.thread.models import Thread
from app.domain.thread.recycle import recycle_due
from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)

AGENT = "cheese-0123456789ab"
OTHER_AGENT = "cheese-2"

#: 一天前报的错：还没到三天，灯还亮着。
YESTERDAY = timedelta(days=1)

#: 四天前报的错：过了三天，该收了。
LONG_AGO = timedelta(days=4)


def _project(client) -> str:
    r = post_project(client, json={"name": "Recycle"}, owner="alice")
    assert r.status_code == 200, r.text
    pid = r.json()["data"]["id"]
    for handle in ("bob", "carol"):
        join_project_team(client, pid, handle)
    return pid


def _room(client, project_id: str, title: str = "房间") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _broke(
    client, project_id: str, conversation_id: str, *, ago: timedelta
) -> datetime:
    """芝士在那一刻留下一条自己的话（把这一轮认到它头上），平台随即记下坏了。"""
    turn = uuid.uuid4()
    at = datetime.now(UTC) - ago

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(conversation_id),
                    turn_id=turn,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=AGENT,
                    content="…",
                    # 5 秒前说过话：这一轮认到它头上，而那句话在失败之前。
                    created_at=at - timedelta(seconds=5),
                )
            )
            s.add(
                Block(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(conversation_id),
                    turn_id=turn,
                    kind=BlockKind.message,
                    author_type=AuthorType.platform,
                    author=AGENT,
                    content="这一轮没跑完",
                    created_at=at,
                    meta={"event_type": "turn_failed", "severity": "error"},
                )
            )
            await s.commit()

    asyncio.run(_add())
    return at


def _sweep(client) -> int:
    """跑一次回收扫描。自己的引擎（`client` 的请求跑在另一条循环上）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.domain.agent.session_state import running_conversations
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)

        return asyncio.run(recycle_due(factory, running=running_conversations))
    finally:
        asyncio.run(engine.dispose())


def _rows(client, room_id: str) -> dict:
    r = client.get(f"/topics/{room_id}/threads", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _room_row(client, project_id: str, room_id: str) -> dict:
    r = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return next(row for row in r.json()["data"]["data"] if row["id"] == room_id)


def _incomplete(row: dict) -> datetime | None:
    at = row["incomplete_since"]
    return None if at is None else datetime.fromisoformat(at)


def _said(client, topic_id: str, handle: str, content: str) -> dict:
    return post_message(client, topic_id, handle, {"content": content})


def _recycled_at(client, thread_id: str) -> datetime | None:
    async def _read():
        async with client.test_factory() as s:
            return await s.scalar(
                select(Thread.recycled_at).where(Thread.id == uuid.UUID(thread_id))
            )

    return asyncio.run(_read())


def _join(client, room_id: str, handle: str) -> None:
    """这个人加入这个频道。别人的「恢复」被拒是因为这条支线不是他开的，而不是因为
    他还没加入 —— 没加入的会被另一条判据（`channelJoinToPost`）先拦下。"""
    r = client.post(f"/topics/{room_id}/join", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text


def _restore(client, thread_id: str, handle: str = "alice"):
    return client.post(
        f"/topics/{thread_id}/thread/restore", headers=session_auth_headers(handle)
    )


def test_a_branch_nobody_came_back_to_is_recycled_and_the_room_stops_glowing(client):
    """四天前报的错没人接手：收进「已回收」，那间房不再红。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=LONG_AGO)

    # 灯早就灭了（三天一到就不亮，独立于收没收）；收是把这条支线从列表里挪开。
    assert _incomplete(_room_row(client, project, room)) is None
    assert _recycled_at(client, thread) is None

    assert _sweep(client) == 1

    row = _room_row(client, project, room)
    assert _incomplete(row) is None
    assert _recycled_at(client, thread) is not None
    lists = _rows(client, room)
    assert [t["id"] for t in lists["recycled"]] == [thread]
    assert lists["open"] == []


def test_a_failure_inside_the_window_still_lights_the_row(client):
    """一天前报的错：还在三天里，灯照亮，也不收。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=YESTERDAY)

    assert _sweep(client) == 0

    assert _incomplete(_room_row(client, project, room)) is not None
    assert _recycled_at(client, thread) is None
    assert [t["id"] for t in _rows(client, room)["open"]] == [thread]


def test_the_rooms_own_line_gives_up_after_the_same_three_days(client):
    """频道自己那条线的红灯同样满三天不再亮 —— 它没有支线可收。"""
    project = _project(client)
    room = _room(client, project)
    _broke(client, project, room, ago=LONG_AGO)

    assert _incomplete(_room_row(client, project, room)) is None


def test_a_branch_whose_session_is_running_is_not_recycled(client):
    """正跑着的支线不是死掉的，只是慢：这一趟不收它。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=LONG_AGO)
    _mark_running(client, thread)

    assert _sweep(client) == 0

    assert _recycled_at(client, thread) is None
    assert _incomplete(_room_row(client, project, room)) is None


def _mark_running(client, thread_id: str) -> None:
    """这个对话里的会话报着在跑（`session_states`）。"""
    from app.domain.agent.models import SessionState

    async def _add() -> None:
        async with client.test_factory() as s:
            s.add(
                SessionState(
                    conversation_id=uuid.UUID(thread_id),
                    agent_handle=AGENT,
                    harness="claude-code",
                    worker_epoch=1,
                    worker_status="running",
                    changed_at=datetime.now(UTC),
                )
            )
            await s.commit()

    asyncio.run(_add())


def test_reading_a_recycled_branch_still_works_and_saying_something_brings_it_back(
    client,
):
    """收起来不是删：读得到，说一句话就回到默认那一栏。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _said(client, thread, "alice", "这条要多久？")
    _broke(client, project, thread, ago=LONG_AGO)
    assert _sweep(client) == 1

    blocks = client.get(
        f"/topics/{thread}/blocks", headers=session_auth_headers("alice")
    )
    assert blocks.status_code == 200, blocks.text
    assert any(b["content"] == "这条要多久？" for b in blocks.json()["data"]["data"])

    _said(client, thread, "alice", "接着看")
    wait_work_idle()

    assert _recycled_at(client, thread) is None
    assert [t["id"] for t in _rows(client, room)["open"]] == [thread]


def test_who_may_restore_it(client):
    """开这条支线的人和频道管理员能点「恢复」，别人不能。"""
    project = _project(client)
    room = _room(client, project, "管理员的房")
    _join(client, room, "carol")
    # 支线由 alice 开（她也是频道的管理员）。
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=LONG_AGO)
    assert _sweep(client) == 1

    refused = _restore(client, thread, "carol")
    assert refused.status_code == 403, refused.text
    assert _recycled_at(client, thread) is not None

    assert _restore(client, thread).status_code == 200
    assert _recycled_at(client, thread) is None


def test_the_person_who_ran_it_can_restore_it_in_a_room_they_do_not_manage(client):
    """不是管理员的那个开的人也能放回来：这条支线是他开的。"""
    project = _project(client)
    room = _room(client, project)
    _join(client, room, "bob")
    _join(client, room, "carol")
    thread = in_thread(client, room, "bob")
    _broke(client, project, thread, ago=LONG_AGO)
    assert _sweep(client) == 1

    assert _restore(client, thread, "carol").status_code == 403
    assert _restore(client, thread, "bob").status_code == 200
    assert _recycled_at(client, thread) is None


def test_restoring_it_does_not_put_the_room_back_red(client):
    """「恢复」是开门，不是重新点灯：那次失败已经过了时限，灯不会跟着回来。"""
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=LONG_AGO)
    assert _sweep(client) == 1
    assert _incomplete(_room_row(client, project, room)) is None

    assert _restore(client, thread).status_code == 200

    assert _incomplete(_room_row(client, project, room)) is None
    # 但这条支线还在默认那一栏里。
    assert [t["id"] for t in _rows(client, room)["open"]] == [thread]
    assert _rows(client, room)["recycled"] == []


def test_a_recycled_branch_is_out_of_the_rooms_units_until_it_comes_back(client):
    """收起来的那条支线整个出了频道那一行：里面再报错也不再点亮它，直到它回来。

    这是「收起来」的意思 —— 一条没人接手的支线不再参与任何一行的判断。要它重新算数，
    先把它放回来；那之后又有一次没人恢复的失败，是新的一个钟。
    """
    project = _project(client)
    room = _room(client, project)
    thread = in_thread(client, room, "alice")
    _broke(client, project, thread, ago=LONG_AGO)
    assert _sweep(client) == 1

    _broke(client, project, thread, ago=timedelta(minutes=5))
    wait_work_idle()
    assert _incomplete(_room_row(client, project, room)) is None

    assert _restore(client, thread).status_code == 200
    at = _broke(client, project, thread, ago=timedelta(minutes=4))
    wait_work_idle()

    row = _room_row(client, project, room)
    assert _incomplete(row) is not None
    assert abs(_incomplete(row) - at) < timedelta(seconds=1)
