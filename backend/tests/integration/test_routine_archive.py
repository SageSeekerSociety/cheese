"""归档带走执行：对账时房间里留一句话，规则的主人收到一条通知；规则本身不动。

归档不写规则那一行（结论：取消归档后从下一个时刻继续，中间错过的记成 skipped），
所以「已随话题归档停止」只能由房间答 —— 接口回给前端的 ``room_archived`` 就是从
``topic.status`` 推出来的。

说话的是对账而不是归档那个接口：归档是别处做的动作（手工、整个项目、脚本），
认得它的只有房间的状态，所以每次 ``sweep`` 问一遍哪个房间的话还没说
（``RoutineService.announce_archived_rooms``），按归档这一轮去重。
"""

import uuid

from sqlalchemy import select

from app.domain.notification.models import Notification
from app.domain.routine.models import Routine, RoutineState
from tests.integration.test_routines import (
    OWNER,
    PERSON,
    _make_due,
    _project,
    _room,
    _runs,
    _sweep,
    _weekly,
)


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _archive(client, room, headers=PERSON):
    return client.post(f"/topics/{room}/archive", json={}, headers=headers)


def _unarchive(client, room, headers=PERSON):
    return client.post(f"/topics/{room}/unarchive", json={}, headers=headers)


def _stopped_lines(client, room) -> list:
    from app.domain.block.models import Block, BlockKind

    async def go(session):
        rows = await session.scalars(
            select(Block).where(
                Block.conversation_id == uuid.UUID(room), Block.kind == BlockKind.event
            )
        )
        return [b for b in rows if b.meta.get("event_type") == "routine_stopped"]

    return _db(client, go)


def _notices_for(client, handle):
    async def go(session):
        rows = await session.scalars(
            select(Notification).where(Notification.recipient_handle == handle)
        )
        return list(rows)

    return _db(client, go)


def test_archiving_a_room_says_so_in_the_room_and_tells_each_owner(client):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    assert rule["state"] == "active"
    assert rule["room_archived"] is False

    archived = _archive(client, room)
    assert archived.status_code == 200, archived.text
    # 归档那一刻不说：接口不碰周期任务那一域，说话的是下一次对账。
    assert _stopped_lines(client, room) == []

    result, _ = _sweep(client)
    assert result["stopped"] == 1, result

    lines = _stopped_lines(client, room)
    assert len(lines) == 1, lines
    assert lines[0].content == "1 条规则已随归档停止"
    assert lines[0].meta["routine_ids"] == [rule["id"]]

    notices = [n for n in _notices_for(client, OWNER) if "周期任务" in n.title]
    assert len(notices) == 1, notices
    assert rule["title"] in notices[0].title
    # 归属那一列是一个会话 id，房间自己就是一条会话，所以存的就是房间。
    assert str(notices[0].conversation_id) == room

    # 规则自己的状态没动，变的只是房间 —— 由 topic.status 推。
    async def state_of(session):
        return await session.scalar(
            select(Routine.state).where(Routine.id == uuid.UUID(rule["id"]))
        )

    assert _db(client, state_of) == RoutineState.active.value

    after = client.get(f"/routines/{rule['id']}", headers=PERSON).json()["data"]
    assert after["state"] == "active"
    assert after["room_archived"] is True
    listed = client.get(f"/projects/{project}/routines", headers=PERSON).json()["data"][
        "data"
    ]
    assert next(r for r in listed if r["id"] == rule["id"])["room_archived"] is True


def test_the_room_is_told_once_per_archive_but_every_episode(client):
    """同一段归档只说一次（项目归档把房间又交了一遍也是），取消归档后再归档
    是新的一段 —— 那句话说的是这一次。"""
    project = _project(client)
    room = _room(client, project)
    _weekly(client, room, headers=PERSON)

    assert _archive(client, room).status_code == 200
    _sweep(client)
    assert len(_stopped_lines(client, room)) == 1

    # 幂等：同一段里再说一遍是没有的事 —— 对账每隔几分钟就来一次。
    _sweep(client)
    assert _archive(client, room).status_code == 200
    _sweep(client)
    assert len(_stopped_lines(client, room)) == 1

    assert _unarchive(client, room).status_code == 200
    assert _archive(client, room).status_code == 200
    _sweep(client)
    assert len(_stopped_lines(client, room)) == 2, "第二段归档没有留下自己的那一行"

    # 项目归档把每个房间都交了出来：这个房间已经在归档里，不再被说一遍。
    with_project = client.post(f"/projects/{project}/archive", json={}, headers=PERSON)
    assert with_project.status_code == 200, with_project.text
    _sweep(client)
    assert len(_stopped_lines(client, room)) == 2


def test_a_room_without_active_rules_is_left_alone(client):
    project = _project(client)
    room = _room(client, project)
    # 只有一份草稿，没有启用中的规则。
    drafted = _weekly(client, room, owner_handle=OWNER).json()["data"]
    assert drafted["state"] == "draft"

    assert _archive(client, room).status_code == 200
    _sweep(client)
    assert _stopped_lines(client, room) == []


def test_unarchiving_lets_the_rule_run_from_the_next_moment(client):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    assert _archive(client, room).status_code == 200
    _make_due(client, rule["id"])
    (_, runner) = _sweep(client)
    assert runner.submitted == [], "归档的房间还在跑"
    assert _runs(client, rule["id"]) == []

    assert _unarchive(client, room).status_code == 200
    assert (
        client.get(f"/routines/{rule['id']}", headers=PERSON).json()["data"][
            "room_archived"
        ]
        is False
    )
    _make_due(client, rule["id"])
    (_, runner) = _sweep(client)
    assert len(runner.submitted) == 1, "取消归档后没有从下一个时刻继续"
    assert len(_runs(client, rule["id"])) == 1
