"""9aac1f78ad6d: a version handed in before the release is told in its channel.

The rules, as a person would state them:

- A version still waiting for review, on a task opened from a channel's main
  line, gets the line the release would have written when it was handed in:
  dated then, from the task's AI teammate, carrying its card and version.
- A version already decided gets no line; nor does one that already has it.
- Writing those lines lights no channel for someone who had read all of it,
  and keeps the place of someone who had not.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seat_handle,
    seed_agent,
    seed_room,
)

BEFORE = "e9d4b023c781"
AFTER = "9aac1f78ad6d"


def _say(db: ReplayDatabase, project, room, content: str, *, at: datetime) -> uuid.UUID:
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'message', 'participant', 'alice', $4, '[]', $5, $5)",
        block,
        project,
        room,
        content,
        at,
    )
    return block


def _task(db: ReplayDatabase, project, room, origin, *, title: str, seat: str):
    task = uuid.uuid4()
    db.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, title_source, status,"
        " agent_handle, upgraded_from_block_id, source_block_id, created_at,"
        " updated_at) VALUES ($1, $2, $3, $4, 'human', 'open', $5, $6, $6,"
        " now(), now())",
        task,
        project,
        room,
        title,
        seat,
        origin,
    )
    return task


def _card(db: ReplayDatabase, room, task, *, status: str, at: datetime) -> uuid.UUID:
    card = uuid.uuid4()
    db.execute(
        "INSERT INTO accept_cards (id, topic_id, task_id, routing_reason, status,"
        " note, change_subject, created_at, updated_at)"
        " VALUES ($1, $2, $3, '', $4, '', 'feat: shorter form', $5, $5)",
        card,
        room,
        task,
        status,
        at,
    )
    return card


def _cursor(db: ReplayDatabase, room, who: str, seq: int) -> None:
    db.execute(
        "INSERT INTO topic_read_states (id, topic_id, user_handle, last_read_seq,"
        " created_at, updated_at) VALUES ($1, $2, $3, $4, now(), now())",
        uuid.uuid4(),
        room,
        who,
        seq,
    )


def _newest_seq(db: ReplayDatabase, room) -> int:
    return db.fetchval("SELECT max(seq) FROM blocks WHERE conversation_id = $1", room)


def test_a_waiting_version_is_told_in_its_main_line_without_lighting_it():
    filed = datetime(2026, 11, 10, 9, tzinfo=UTC)
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        teammate = seed_agent(db, project, "helper")
        seat = seat_handle(teammate)
        asked = _say(db, project, room, "把报名表做短", at=filed - timedelta(hours=1))
        waiting = _task(db, project, room, asked, title="报名表", seat=seat)
        _card(db, room, waiting, status="rejected", at=filed - timedelta(minutes=30))
        second = _card(db, room, waiting, status="pending", at=filed)
        decided = _task(db, project, room, asked, title="首页", seat=seat)
        _card(db, room, decided, status="accepted", at=filed)
        _say(db, project, room, "大家周五见", at=filed + timedelta(days=1))
        newest = _newest_seq(db, room)
        _cursor(db, room, "bob", newest)
        _cursor(db, room, "carol", newest - 1)

        db.upgrade(AFTER)

        told = db.fetch(
            "SELECT author, content, meta::text AS meta, created_at FROM blocks"
            " WHERE conversation_id = $1 AND meta ->> 'task_result' IS NOT NULL",
            room,
        )
        assert len(told) == 1
        [line] = told
        meta = json.loads(line["meta"])
        assert meta["task_result"] == str(waiting)
        assert meta["delivery"] == {
            "card_id": str(second),
            "subject": "feat: shorter form",
            "version": 2,
        }
        assert meta["i18n"]["content"]["key"] == "taskFiledForReview"
        assert line["content"] == "「报名表」已提交审阅"
        assert line["author"] == seat
        assert line["created_at"] == filed

        # Bob had read everything and still has; Carol keeps her place.
        cursors = dict(
            db.fetch(
                "SELECT user_handle, last_read_seq FROM topic_read_states"
                " WHERE topic_id = $1",
                room,
            )
        )
        assert cursors == {"bob": _newest_seq(db, room), "carol": newest - 1}

        # Run again, it finds every waiting version already told.
        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert (
            db.fetchval(
                "SELECT count(*) FROM blocks WHERE conversation_id = $1"
                " AND meta ->> 'task_result' IS NOT NULL",
                room,
            )
            == 1
        )
