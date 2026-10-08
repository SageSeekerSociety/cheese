"""What migration c3a8e5f1d702 does to a channel's history.

The rules, stated before it was written:

- a task an AI teammate proposed in a 支线 hangs under the message that 支线 is
  under; one made from a reply in a 支线 hangs under that 支线's message;
- the main line no longer says what happened to a task made from a message: its
  created, started and closed lines, and the lines about its deliveries, move
  into the task's own conversation, and nothing else moves;
- a task made on its own keeps its created line in the main line;
- the 「更新了这个频道的任务」 lines go;
- every card filed before is its task's last step.
"""

import json
import uuid

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "d3a8f6b2c917"
AFTER = "c3a8e5f1d702"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _task(db: ReplayDatabase, project, room, origin=None) -> uuid.UUID:
    task = uuid.uuid4()
    db.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, status, created_at,"
        " updated_at, upgraded_from_block_id) VALUES ($1, $2, $3, 'work', 'open',"
        " now(), now(), $4)",
        task,
        project,
        room,
        origin,
    )
    return task


def _block(db: ReplayDatabase, project, where, meta=None, *, at="now()") -> uuid.UUID:
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, meta, created_at, updated_at)"
        f" VALUES ($1, $2, $3, $4, $5, 'alice', 'x', '[]', $6::json, {at}, {at})",
        block,
        project,
        where,
        "event" if meta else "message",
        "platform" if meta else "participant",
        json.dumps(meta) if meta else None,
    )
    return block


def _thread(db: ReplayDatabase, project, room, root) -> uuid.UUID:
    thread = uuid.uuid4()
    db.execute(
        "INSERT INTO threads (id, project_id, room_id, root_block_id, reply_count,"
        " created_by, created_at) VALUES ($1, $2, $3, $4, 0, 'alice', now())",
        thread,
        project,
        room,
        root,
    )
    return thread


def _where(db: ReplayDatabase, block) -> uuid.UUID | None:
    return db.fetchval("SELECT conversation_id FROM blocks WHERE id = $1", block)


def test_a_channels_task_history_moves_into_its_tasks(db):
    project, room = seed_room(db)
    asked = _block(db, project, room, at="now() - interval '1 hour'")
    thread = _thread(db, project, room, asked)
    reply = _block(db, project, thread)
    from_message = _task(db, project, room, origin=asked)
    from_reply = _task(db, project, room, origin=reply)
    on_its_own = _task(db, project, room)
    proposed = _task(db, project, room)
    db.execute(
        "INSERT INTO task_proposals (id, project_id, room_id, conversation_id,"
        " title, summary, proposed_by, state, task_id, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, 't', 's', 'cheese', 'accepted', $5, now(), now())",
        uuid.uuid4(),
        project,
        room,
        thread,
        proposed,
    )
    created = _block(
        db, project, room, {"action": "task_created", "task_id": str(from_message)}
    )
    own_created = _block(
        db, project, room, {"action": "task_created", "task_id": str(on_its_own)}
    )
    started = _block(
        db, project, room, {"action": "task_started", "task_id": str(on_its_own)}
    )
    db.execute(
        "INSERT INTO accept_cards (id, topic_id, task_id, reviewer_handle,"
        " routing_reason, status, note, pr_number, delivered_task_ids, nudge_state,"
        " rebase_count, gate_output, created_at, updated_at) VALUES ($1, $2, $3,"
        " 'alice', '', 'accepted', '', 45, '[]', '{}', 0, '', now(), now())",
        uuid.uuid4(),
        room,
        from_message,
    )
    landed = _block(
        db,
        project,
        room,
        {
            "event_type": "accept_done",
            "i18n": {"content": {"key": "acceptDone", "params": {"pr": 45}}},
        },
    )
    other_pr = _block(
        db,
        project,
        room,
        {
            "event_type": "accept_done",
            "i18n": {"content": {"key": "acceptDone", "params": {"pr": 46}}},
        },
    )
    said_nothing = _block(db, project, room, {"action": "topics"})

    db.upgrade(AFTER)

    origins = {
        row["id"]: row["upgraded_from_block_id"]
        for row in db.fetch("SELECT id, upgraded_from_block_id FROM tasks")
    }
    assert origins[from_message] == asked
    assert origins[from_reply] == asked
    assert origins[proposed] == asked
    assert origins[on_its_own] is None
    assert _where(db, created) == from_message
    assert _where(db, own_created) == room
    assert _where(db, started) == on_its_own
    assert _where(db, landed) == from_message
    assert _where(db, other_pr) == room
    assert _where(db, asked) == room
    assert _where(db, said_nothing) is None
    assert db.fetchval("SELECT bool_and(completes_task) FROM accept_cards") is True
