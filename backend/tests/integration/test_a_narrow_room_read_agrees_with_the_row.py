"""窄读交出来的 `ListedRoom` 和整行，说的是同一件事。

「待我处理」把房间收窄到真要摆的那几个之后，读的是列投影
（`topic.repositories.ListedRoom`），不是整行 —— 整行还背着环境文档和简介两列
JSON，摆一屏清单一个字都不读。少读一列、或者把哪一列接到别的属性上，页面上就是
同一个房间说出两种话：`facts_for_room` 靠 `status` 挑格子、靠 `id` 认这是不是
正在跑的那个房间、靠 `accepted_at` 说这一步交没交出去。

所以这里在真库上按六位逐一对照，而不是数「字段个数没少」：投影出来的那一份，
和 `TopicRepository.get` 读回来的整行，折出来的事实必须一模一样。
"""

import uuid

from sqlalchemy import text

from app.domain.room_task.presentation import facts_for_room
from app.domain.topic.repositories import TopicRepository
from tests.integration.conftest import a_team


async def _project(db_session, handle: str = "alice") -> uuid.UUID:
    project_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO projects"
            " (id, name, owner_handle, team_id, ai_mode, settings,"
            " created_at, updated_at)"
            " VALUES (:id, :name, :owner, :team, 'off', '{}', now(), now())"
        ),
        {
            "id": project_id,
            "name": "Narrow room reads",
            "owner": handle,
            "team": await a_team(db_session),
        },
    )
    return project_id


async def _room(
    db_session, project_id: uuid.UUID, title: str, *, status: str = "active"
) -> uuid.UUID:
    room_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO topics"
            " (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES (:id, :project, :title, 'topic', :status, false,"
            " now(), now())"
        ),
        {
            "id": room_id,
            "project": project_id,
            "title": title,
            "status": status,
        },
    )
    return room_id


async def _accepted(db_session, room_id: uuid.UUID) -> None:
    await db_session.execute(
        text("UPDATE topics SET accepted_at = now() WHERE id = :id"),
        {"id": room_id},
    )


def test_a_listed_room_carries_exactly_what_the_row_carries(db_session, _portal):
    """同一个房间，窄读拿回来的六位和整行逐位相同。"""

    async def ask() -> None:
        project_id = await _project(db_session)
        accepted = await _room(db_session, project_id, "预算复核")
        await _accepted(db_session, accepted)
        archived = await _room(db_session, project_id, "归档的那个", status="archived")
        absent = uuid.uuid4()

        repository = TopicRepository(db_session)
        listed = {
            room.id: room
            for room in await repository.listed_by_ids(
                [accepted, archived, absent, accepted]
            )
        }
        # 点不到的 id 就是没有这一行，不是错误；同一个 id 给两遍也是同一行。
        assert set(listed) == {accepted, archived}

        for room_id in (accepted, archived):
            row = await repository.get(room_id)
            assert row is not None
            narrowed = listed[room_id]
            assert (
                narrowed.project_id,
                narrowed.title,
                narrowed.status,
                narrowed.accepted_at,
                narrowed.updated_at,
            ) == (
                row.project_id,
                row.title,
                row.status,
                row.accepted_at,
                row.updated_at,
            )
            # 折出来的事实：status 挑格子、id 说这是不是正在跑的那个房间、
            # accepted_at 说这一步交没交出去。
            assert facts_for_room(narrowed, {accepted}) == facts_for_room(
                row, {accepted}
            )

        assert await repository.listed_by_ids([]) == []

    _portal.call(ask)
