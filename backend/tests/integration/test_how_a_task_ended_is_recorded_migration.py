"""59f3a1c11b34: how a task ended, written as the teammate's words, becomes the
platform's record of it.

The rules, as a person would state them:

- A line saying how a task ended — closed with a conclusion, accepted, or
  closed — is no longer a message of the task's AI teammate: it is a line the
  platform recorded about the task. That holds for the lines written before
  their sentence carried its key, too.
- A line saying a step landed and the task goes on is not about the end and
  stays the teammate's message.
- Each line stays where it was in the conversation, and nobody's place in it
  moves.
- A 支线 counts as its replies only the messages in it.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_room,
    seed_task,
)

BEFORE = "f02ff6f94b03"
AFTER = "59f3a1c11b34"

SEAT = "cheese-0a1b2c3d4e5f"


def _line(
    db: ReplayDatabase,
    project,
    conversation,
    *,
    author: str,
    content: str,
    at: datetime,
    meta: dict | None = None,
) -> uuid.UUID:
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, meta, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'message', 'participant', $4, $5, '[]', $6::json,"
        " $7, $7)",
        block,
        project,
        conversation,
        author,
        content,
        json.dumps(meta) if meta is not None else None,
        at,
    )
    return block


def _keyed(task, key: str, **params) -> dict:
    return {
        "task_result": str(task),
        "i18n": {"content": {"key": key, "params": params}},
    }


def _row(db: ReplayDatabase, block) -> dict:
    row = db.fetchrow(
        "SELECT kind, author_type, author, seq, created_at, meta::text AS meta"
        " FROM blocks WHERE id = $1",
        block,
    )
    return {**dict(row), "meta": json.loads(row["meta"])}


def test_how_a_task_ended_becomes_the_platform_s_record_in_place():
    at = datetime(2026, 11, 12, 9, tzinfo=UTC)
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        asked = _line(db, project, room, author="alice", content="把报名表做短", at=at)

        def said(conversation, content, minutes, meta):
            return _line(
                db,
                project,
                conversation,
                author=SEAT,
                content=content,
                at=at + timedelta(minutes=minutes),
                meta=meta,
            )

        completed = said(
            room,
            "任务「报名表」已关闭：做完了",
            1,
            _keyed(task, "taskCompleted", title="报名表", conclusion="做完了"),
        )
        landed = said(
            room,
            "「报名表」已采纳 · PR #7",
            2,
            _keyed(task, "taskLanded", title="报名表", pr=7),
        )
        # Written before the sentence carried its key.
        old_closed = said(room, "任务「报名表」已关闭", 3, {"task_result": str(task)})
        old_landed = said(room, "「报名表」已采纳", 4, {"task_result": str(task)})
        old_step = said(
            room,
            "「报名表」的这次交付已采纳 · PR #6，任务继续进行",
            5,
            {"task_result": str(task)},
        )
        step = said(
            room,
            "「报名表」的这次交付已采纳，任务继续进行",
            6,
            _keyed(task, "taskStepLandedNoPr", title="报名表"),
        )
        thread = uuid.uuid4()
        db.execute(
            "INSERT INTO threads (id, project_id, room_id, root_block_id,"
            " created_by, created_at) VALUES ($1, $2, $3, $4, 'alice', now())",
            thread,
            project,
            room,
            asked,
        )
        reply_at = at + timedelta(minutes=7)
        _line(db, project, thread, author="bob", content="再短一点", at=reply_at)
        in_thread = said(
            thread,
            "任务「报名表」已关闭",
            8,
            _keyed(task, "taskClosed", title="报名表"),
        )
        turned = (completed, landed, old_closed, old_landed, in_thread)
        before = {b: _row(db, b) for b in (*turned, old_step, step)}
        db.execute(
            "INSERT INTO topic_read_states (id, topic_id, user_handle, last_read_seq,"
            " created_at, updated_at) VALUES ($1, $2, 'bob', $3, now(), now())",
            uuid.uuid4(),
            room,
            before[landed]["seq"],
        )

        db.upgrade(AFTER)

        for block in turned:
            row = _row(db, block)
            assert (row["kind"], row["author_type"], row["author"]) == (
                "event",
                "platform",
                "system",
            )
            assert row["meta"]["event_type"] == "task_ended"
            assert row["meta"]["task_id"] == str(task)
            assert "task_result" not in row["meta"]
            assert row["meta"].get("i18n") == before[block]["meta"].get("i18n")
            assert (row["seq"], row["created_at"]) == (
                before[block]["seq"],
                before[block]["created_at"],
            )
        for block in (old_step, step):
            assert _row(db, block) == before[block]

        replies = db.fetchrow(
            "SELECT reply_count, last_reply_at FROM threads WHERE id = $1", thread
        )
        assert (replies["reply_count"], replies["last_reply_at"]) == (1, reply_at)
        assert (
            db.fetchval(
                "SELECT last_read_seq FROM topic_read_states WHERE topic_id = $1",
                room,
            )
            == before[landed]["seq"]
        )

        # Run again, it finds nothing left to turn and changes nothing.
        after = {b: _row(db, b) for b in (*turned, old_step, step)}
        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert {b: _row(db, b) for b in after} == after
        assert db.fetchval("SELECT reply_count FROM threads WHERE id = $1", thread) == 1
