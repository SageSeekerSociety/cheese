"""f02ff6f94b03: a hand-in and a return written as the teammate's words become
the platform's record of them.

The rules, as a person would state them:

- A line saying a version was handed in, or returned, is no longer a message of
  the task's AI teammate: it is a line the platform recorded, about the task,
  a hand-in still naming the card it filed and which version.
- It stays where it was in the conversation, and nobody's place in it moves.
- The line saying how the task ended is the teammate's own words and stays one.
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

BEFORE = "a7c3e9f1b5d2"
AFTER = "f02ff6f94b03"

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


def _handed_in(task, card, version: int) -> dict:
    return {
        "task_result": str(task),
        "delivery": {
            "card_id": str(card),
            "subject": "feat: shorter",
            "version": version,
        },
        "i18n": {
            "content": {"key": "taskFiledForReview", "params": {"title": "报名表"}}
        },
    }


def _returned(task) -> dict:
    return {
        "task_result": str(task),
        "i18n": {
            "content": {
                "key": "taskReturned",
                "params": {"who": "<@alice>", "title": "报名表"},
            }
        },
    }


def _row(db: ReplayDatabase, block) -> dict:
    row = db.fetchrow(
        "SELECT kind, author_type, author, seq, created_at, meta::text AS meta"
        " FROM blocks WHERE id = $1",
        block,
    )
    return {**dict(row), "meta": json.loads(row["meta"])}


def test_hand_ins_and_returns_become_the_platform_s_record_in_place():
    at = datetime(2026, 11, 12, 9, tzinfo=UTC)
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        card = uuid.uuid4()
        asked = _line(db, project, room, author="alice", content="把报名表做短", at=at)
        handed = _line(
            db,
            project,
            room,
            author=SEAT,
            content="「报名表」已提交审阅",
            at=at + timedelta(minutes=1),
            meta=_handed_in(task, card, 1),
        )
        returned = _line(
            db,
            project,
            room,
            author=SEAT,
            content="<@alice> 退回了「报名表」",
            at=at + timedelta(minutes=2),
            meta=_returned(task),
        )
        ended = _line(
            db,
            project,
            room,
            author=SEAT,
            content="「报名表」做完了",
            at=at + timedelta(minutes=3),
            meta={
                "task_result": str(task),
                "i18n": {"content": {"key": "taskCompleted", "params": {}}},
            },
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
        reply_at = at + timedelta(minutes=4)
        _line(db, project, thread, author="bob", content="再短一点", at=reply_at)
        in_thread = _line(
            db,
            project,
            thread,
            author=SEAT,
            content="「报名表」已提交审阅",
            at=at + timedelta(minutes=5),
            meta=_handed_in(task, card, 2),
        )
        before = {b: _row(db, b) for b in (handed, returned, ended, in_thread)}
        db.execute(
            "INSERT INTO topic_read_states (id, topic_id, user_handle, last_read_seq,"
            " created_at, updated_at) VALUES ($1, $2, 'bob', $3, now(), now())",
            uuid.uuid4(),
            room,
            before[returned]["seq"],
        )

        db.upgrade(AFTER)

        for block, event_type in (
            (handed, "task_filed"),
            (returned, "task_returned"),
            (in_thread, "task_filed"),
        ):
            row = _row(db, block)
            assert (row["kind"], row["author_type"], row["author"]) == (
                "event",
                "platform",
                "system",
            )
            assert row["meta"]["event_type"] == event_type
            assert row["meta"]["task_id"] == str(task)
            assert "task_result" not in row["meta"]
            # The sentence stays, so the line still reads the task's name.
            assert row["meta"]["i18n"] == before[block]["meta"]["i18n"]
            assert (row["seq"], row["created_at"]) == (
                before[block]["seq"],
                before[block]["created_at"],
            )
        assert _row(db, handed)["meta"]["delivery"] == {
            "card_id": str(card),
            "version": 1,
        }
        assert _row(db, in_thread)["meta"]["delivery"]["version"] == 2
        assert "delivery" not in _row(db, returned)["meta"]
        assert _row(db, ended)["kind"] == "message"
        assert _row(db, ended)["author"] == SEAT

        replies = db.fetchrow(
            "SELECT reply_count, last_reply_at FROM threads WHERE id = $1", thread
        )
        assert (replies["reply_count"], replies["last_reply_at"]) == (1, reply_at)
        assert (
            db.fetchval(
                "SELECT last_read_seq FROM topic_read_states WHERE topic_id = $1",
                room,
            )
            == before[returned]["seq"]
        )

        # Run again, it finds nothing left to turn and changes nothing.
        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert db.fetchval("SELECT reply_count FROM threads WHERE id = $1", thread) == 1
        assert _row(db, handed)["meta"]["delivery"]["version"] == 1
