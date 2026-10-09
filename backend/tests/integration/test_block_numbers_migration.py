"""Blocks numbered in the order they were stored, and read cursors that are
numbers, from a database written before either existed.

The blocks a conversation already held are numbered in the order its room shows
them; every block stored from the first migration on takes the next number, the
release still serving during the deploy included, which writes no number. A read
cursor counts the same messages as unread that its time did.
"""

import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "bc82f9d6481a"
COUNTER = "1a96fb7b05db"
NUMBERED = "5f1b7d54bffa"
HEAD = "4e8f1c1e2b22"

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _block(db, project, conversation, *, at: datetime, kind: str = "message") -> str:
    """A block written the way a release that knows no numbers writes one."""
    block = uuid.uuid4()
    db.execute(
        "INSERT INTO blocks (id, project_id, conversation_id, kind, author_type,"
        " author, content, refs, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, 'participant', 'alice', 'x', '[]', $5, $5)",
        block,
        project,
        conversation,
        kind,
        at,
    )
    return str(block)


def _numbers(db, conversation) -> dict[str, int]:
    rows = db.fetch(
        "SELECT id, seq FROM blocks WHERE conversation_id = $1", conversation
    )
    return {str(r["id"]): r["seq"] for r in rows}


def test_blocks_already_written_are_numbered_in_the_order_the_room_shows_them():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        # Stored out of date order: the room shows them by date.
        second = _block(db, project, room, at=T0 + timedelta(seconds=2))
        first = _block(db, project, room, at=T0)
        third = _block(db, project, room, at=T0 + timedelta(seconds=3), kind="event")

        db.upgrade(HEAD)

        assert _numbers(db, room) == {first: 1, second: 2, third: 3}


def test_a_block_written_during_the_deploy_comes_after_all_of_them():
    """The release still serving stores blocks between the migrations, without a
    number of its own. Dated earlier than the ones before it, it is still
    numbered after them: it was stored after them."""
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        held = [
            _block(db, project, room, at=T0 + timedelta(seconds=i)) for i in range(3)
        ]

        db.upgrade(COUNTER)
        during = _block(db, project, room, at=T0 - timedelta(days=1))
        db.upgrade(HEAD)
        after = _block(db, project, room, at=T0 - timedelta(days=2))

        numbers = _numbers(db, room)
        assert [numbers[b] for b in held] == [1, 2, 3]
        assert numbers[during] == 4
        assert numbers[after] == 5


def test_each_conversation_counts_on_its_own():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        elsewhere, other = seed_room(db)
        _block(db, project, room, at=T0)
        _block(db, project, room, at=T0)

        db.upgrade(HEAD)
        first_elsewhere = _block(db, elsewhere, other, at=T0)

        assert _numbers(db, other) == {first_elsewhere: 1}


def test_a_read_cursor_counts_the_same_messages_unread_as_its_time_did():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        _block(db, project, room, at=T0)
        _block(db, project, room, at=T0 + timedelta(seconds=1))
        _block(db, project, room, at=T0 + timedelta(seconds=5))
        for handle, read_at in (
            ("alice", T0 + timedelta(seconds=2)),
            ("bob", T0 - timedelta(days=1)),
            ("carol", datetime(1970, 1, 1, tzinfo=UTC)),
        ):
            db.execute(
                "INSERT INTO topic_read_states (id, topic_id, user_handle,"
                " last_read_at, notify_level, created_at, updated_at)"
                " VALUES ($1, $2, $3, $4, 'mentions', now(), now())",
                uuid.uuid4(),
                room,
                handle,
                read_at,
            )

        db.upgrade(HEAD)

        cursors = {
            r["user_handle"]: r["last_read_seq"]
            for r in db.fetch(
                "SELECT user_handle, last_read_seq FROM topic_read_states"
                " WHERE topic_id = $1",
                room,
            )
        }
        # alice read after the first two; bob and carol before any.
        assert cursors == {"alice": 2, "bob": 0, "carol": 0}


def test_numbering_again_changes_nothing():
    """A deploy that stopped partway runs the numbering again."""
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        _block(db, project, room, at=T0 + timedelta(seconds=1))
        _block(db, project, room, at=T0)
        db.upgrade(NUMBERED)
        before = _numbers(db, room)

        db.downgrade(COUNTER)
        db.upgrade(NUMBERED)

        assert _numbers(db, room) == before
