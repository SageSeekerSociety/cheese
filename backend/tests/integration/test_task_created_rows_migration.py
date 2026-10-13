"""Every task started in a room says so on the room's line (bc82f9d6481a).

Tasks from before the room's line recorded a task starting have no row there,
and the client used to place a marker for each from the room's whole task list.
The migration writes the row a task started today gets, at the moment the task
was created; a task already named on the line, or made from a message (it shows
under the message), is left alone.
"""

import json
import uuid
from datetime import UTC, datetime

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "f17973b3f7b6"
AFTER = "bc82f9d6481a"

STARTED = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)


def _task(
    db: ReplayDatabase,
    project,
    room,
    *,
    title: str = "导出加速",
    title_source: str = "human",
    owner: str | None = "bob",
    created_by: str | None = "alice",
    origin: uuid.UUID | None = None,
) -> uuid.UUID:
    task = uuid.uuid4()
    db.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, title_source, status,"
        " owner_handle, created_by, upgraded_from_block_id, contributor_handles,"
        " created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, 'open', $6, $7, $8, '[]', $9, $9)",
        task,
        project,
        room,
        title,
        title_source,
        owner,
        created_by,
        origin,
        STARTED,
    )
    return task


def _row(
    db: ReplayDatabase, project, room, *, meta: dict | None, kind: str
) -> uuid.UUID:
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, meta, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, 'participant', 'alice', 'x', '[]', $5::json,"
        " now(), now())",
        block,
        project,
        room,
        kind,
        json.dumps(meta) if meta else None,
    )
    return block


def _rows_naming(db: ReplayDatabase, task: uuid.UUID):
    return db.fetch(
        "SELECT conversation_id, kind, author, author_type, content, meta::text,"
        " created_at FROM blocks WHERE meta ->> 'task_id' = $1",
        str(task),
    )


def test_a_task_started_in_a_room_gets_the_row_a_new_one_gets():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = _task(db, project, room)

        db.upgrade(AFTER)

        [row] = _rows_naming(db, task)
        assert row["conversation_id"] == room
        assert (row["kind"], row["author"], row["author_type"]) == (
            "event",
            "system",
            "platform",
        )
        assert row["created_at"] == STARTED
        assert row["content"] == "<@alice> 创建了任务「导出加速」，由 <@bob> 负责"
        assert json.loads(row["meta"]) == {
            "platform": True,
            "action": "task_created",
            "task_id": str(task),
            "i18n": {
                "content": {
                    "key": "taskCreated",
                    "params": {
                        "actor": "<@alice>",
                        "title": "导出加速",
                        "owner": "<@bob>",
                    },
                }
            },
        }


def test_an_unnamed_task_is_said_in_each_readers_words():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = _task(
            db,
            project,
            room,
            title="新任务",
            title_source="placeholder",
            owner=None,
            created_by=None,
        )

        db.upgrade(AFTER)

        [row] = _rows_naming(db, task)
        params = json.loads(row["meta"])["i18n"]["content"]["params"]
        assert params == {
            "actor": "<@system>",
            "title": {"key": "taskUntitled", "params": {}},
            "owner": "",
        }


def test_a_task_the_line_already_names_or_made_from_a_message_is_left_alone():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        named = _task(db, project, room)
        _row(
            db,
            project,
            room,
            kind="event",
            meta={"platform": True, "action": "split", "task_id": str(named)},
        )
        message = _row(db, project, room, meta=None, kind="message")
        from_message = _task(db, project, room, origin=message)

        db.upgrade(AFTER)

        assert len(_rows_naming(db, named)) == 1
        assert _rows_naming(db, from_message) == []


def test_running_it_again_writes_nothing_more():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = _task(db, project, room)

        db.upgrade(AFTER)
        db.downgrade(BEFORE)
        db.upgrade(AFTER)

        assert len(_rows_naming(db, task)) == 1
