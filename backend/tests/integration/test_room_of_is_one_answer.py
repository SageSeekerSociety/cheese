"""「一个会话在哪个房间」有两个函数，只该有一个答案。

``device/owner_reads.py`` 与 ``conversation/services.py`` 各有一个 ``room_of``，
docstring 逐字相同。设备那份是连接 owner 读的：发布会把 owner 留在旧镜像上跑，
所以它用 ``table(...)`` 点名列，不加载 ORM 模型（理由见那个模块的说明）。两份读的
都是 ``tasks`` 与 ``threads`` 两张表，都不为找不到的会话编造房间。

这里钉两件事：房间、任务、支线各问一次，两份答案必须相同；设备那份一次问完——
旧实现先查 ``tasks`` 再查 ``threads``，问一个房间就是两条往返。
"""

import uuid

from sqlalchemy import event, text

from app.domain.conversation import services as conversations
from app.domain.device import owner_reads
from tests.integration.conftest import a_team


async def _project(session, handle: str = "alice") -> uuid.UUID:
    project_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO projects"
            " (id, name, owner_handle, team_id, ai_mode, settings,"
            " created_at, updated_at)"
            " VALUES (:id, :name, :owner, :team, 'off', '{}', now(), now())"
        ),
        {
            "id": project_id,
            "name": "One answer",
            "owner": handle,
            "team": await a_team(session),
        },
    )
    return project_id


async def _room(session, project_id: uuid.UUID) -> uuid.UUID:
    room_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO topics"
            " (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES (:id, :project, 'A room', 'room', 'active', false,"
            " now(), now())"
        ),
        {"id": room_id, "project": project_id},
    )
    return room_id


async def _task(session, project_id: uuid.UUID, room_id: uuid.UUID) -> uuid.UUID:
    task_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tasks"
            " (id, project_id, room_id, title, status, created_at, updated_at)"
            " VALUES (:id, :project, :room, 'A task', 'open', now(), now())"
        ),
        {"id": task_id, "project": project_id, "room": room_id},
    )
    return task_id


async def _thread(session, project_id: uuid.UUID, room_id: uuid.UUID) -> uuid.UUID:
    root = uuid.uuid4()
    thread_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO blocks"
            " (id, project_id, conversation_id, kind, author_type, author,"
            " content, refs, created_at, updated_at)"
            " VALUES (:root, :project, :room, 'message', 'participant', 'alice',"
            " 'hi', '[]', now(), now())"
        ),
        {"root": root, "project": project_id, "room": room_id},
    )
    await session.execute(
        text(
            "INSERT INTO threads"
            " (id, project_id, room_id, root_block_id, created_by, created_at)"
            " VALUES (:id, :project, :room, :root, 'alice', now())"
        ),
        {
            "id": thread_id,
            "project": project_id,
            "room": room_id,
            "root": root,
        },
    )
    return thread_id


def _sync_engine(session):
    bind = session.bind
    return getattr(bind, "sync_engine", None) or bind.engine.sync_engine


def test_a_room_a_task_and_a_thread_get_the_same_answer(db_session, _portal):
    async def ask():
        project_id = await _project(db_session)
        room = await _room(db_session, project_id)
        task = await _task(db_session, project_id, room)
        thread = await _thread(db_session, project_id, room)
        answers = {}
        for conversation_id in (room, task, thread):
            answers[conversation_id] = (
                await owner_reads.room_of(db_session, conversation_id),
                await conversations.room_of(db_session, conversation_id),
            )
        return room, {room, task, thread}, answers

    room, asked, answers = _portal.call(ask)
    # All three were asked: an empty loop would leave nothing to check below.
    assert set(answers) == asked
    for answer in answers.values():
        assert answer == (room, room)


def test_a_conversation_only_the_register_knows_is_not_given_a_room(
    db_session, _portal
):
    """注册表里有一行、两张旧表都没有的形状。

    ``conversations`` 是触发器维护的登记表（没有 room 列），两张旧表才是房间的
    出处。所以这个形状问不出房间，两份答案只能都说「它自己」——谁都不替它猜一个。
    这不是两份实现在分歧：问的是「找不到时谁编造房间」，两边都不许编。
    """

    async def ask():
        project_id = await _project(db_session)
        stranger = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO conversations (id, project_id, kind)"
                " VALUES (:id, :project, 'thread')"
            ),
            {"id": stranger, "project": project_id},
        )
        known = await db_session.scalar(
            text(
                "SELECT"
                " (SELECT count(*) FROM tasks WHERE id = :id)"
                " + (SELECT count(*) FROM threads WHERE id = :id)"
            ).bindparams(id=stranger)
        )
        return (
            stranger,
            known,
            (
                await owner_reads.room_of(db_session, stranger),
                await conversations.room_of(db_session, stranger),
            ),
        )

    stranger, known, answer = _portal.call(ask)
    assert known == 0
    assert answer == (stranger, stranger)


def test_the_device_answer_asks_one_statement(db_session, _portal):
    """同一件事问一次。

    旧实现按 ``tasks``、``threads`` 的顺序各发一条：房间和两张表都不认识的会话
    都要问两遍，一次就是两条往返。改成和 ``_inside`` 相同的 union 之后，每种形状
    都只有一条。
    """

    async def ask():
        project_id = await _project(db_session)
        room = await _room(db_session, project_id)
        task = await _task(db_session, project_id, room)
        thread = await _thread(db_session, project_id, room)
        stranger = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO conversations (id, project_id, kind)"
                " VALUES (:id, :project, 'thread')"
            ),
            {"id": stranger, "project": project_id},
        )

        seen: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        engine = _sync_engine(db_session)
        event.listen(engine, "before_cursor_execute", record)
        try:
            counts = {}
            for name, conversation_id in (
                ("room", room),
                ("task", task),
                ("thread", thread),
                ("stranger", stranger),
            ):
                before = len(seen)
                await owner_reads.room_of(db_session, conversation_id)
                counts[name] = len(seen) - before
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return counts

    assert _portal.call(ask) == {
        "room": 1,
        "task": 1,
        "thread": 1,
        "stranger": 1,
    }
