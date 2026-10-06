"""Turns read inside another turn that ended are ended (migration 08b4bcff4ad2).

A message sent while a session was mid-turn was read inside that turn, and the
backend that saw it read was replaced before the turn ended, so the message's
own turn never got an end and its room stayed busy.

Each test builds a database at the revision before the migration, seeds the
turns and the input ledger as they stood then, upgrades to the migration, and
reads back whether each turn is still running and whether the room is busy.
"""

import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    room_is_busy,
    seed_room,
    seed_turn,
    stopped_at,
)

BEFORE = "c4e8a17b2d90"
AFTER = "08b4bcff4ad2"

SEAT = "cheese-0123456789ab"
AGENT = SEAT


def _turn(
    db: ReplayDatabase,
    room: uuid.UUID,
    *,
    started: datetime,
    stopped: datetime | None = None,
    by_session: bool = False,
) -> uuid.UUID:
    """One delivered turn on ``room``'s seat. A turn the session opened itself
    is authored by its agent; a platform message's by ``system``."""
    return seed_turn(
        db,
        room,
        started=started,
        stopped=stopped,
        author=AGENT if by_session else "system",
        content="" if by_session else "check in",
        resendable=not by_session,
        seat=SEAT,
    )


def _input(
    db: ReplayDatabase,
    project: uuid.UUID,
    room: uuid.UUID,
    work: uuid.UUID,
    *,
    executed_in: uuid.UUID,
    at: datetime,
    echoed: bool = True,
) -> None:
    """The ledger's row for ``work``'s input: echoed by the session under
    ``executed_in``."""
    db.execute(
        "INSERT INTO native_inputs (id, project_id, topic_id, recipient_handle,"
        " harness, native_session_id, input_id, work_id, execution_work_id,"
        " block_ids, seen_block_ids, held_block_ids, released_block_ids,"
        " registered_at, echoed_at) VALUES ($1, $2, $3, $4, 'claude-code',"
        " 'native-session', $5, $5, $6, '[]', '[]', '[]', '[]', $7, $8)",
        uuid.uuid4(),
        project,
        room,
        SEAT,
        work,
        executed_in,
        at,
        at if echoed else None,
    )


def test_a_check_in_read_inside_the_sessions_own_work_frees_its_room():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # The session was working on its own when the check-in arrived, read it
        # at a tool boundary, and finished that work.
        finished = now - timedelta(hours=2, minutes=50)
        own_work = _turn(
            db,
            room,
            started=now - timedelta(hours=3),
            stopped=finished,
            by_session=True,
        )
        check_in = _turn(db, room, started=now - timedelta(hours=2, minutes=58))
        _input(
            db,
            project,
            room,
            check_in,
            executed_in=own_work,
            at=now - timedelta(hours=2, minutes=57),
        )
        assert room_is_busy(db, room)

        db.upgrade(AFTER)

        assert not room_is_busy(db, room)
        # It ends when the work that read it ended.
        assert stopped_at(db, check_in) == finished


def test_a_check_in_whose_reader_has_not_ended_is_left_running():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # Read inside work the session is still doing: it ends when that work
        # does.
        still_working = _turn(
            db, room, started=now - timedelta(minutes=20), by_session=True
        )
        riding = _turn(db, room, started=now - timedelta(minutes=10))
        _input(
            db,
            project,
            room,
            riding,
            executed_in=still_working,
            at=now - timedelta(minutes=9),
        )
        # Sent, but the session never echoed it: whether it was read is unknown.
        ended = _turn(
            db,
            room,
            started=now - timedelta(hours=5),
            stopped=now - timedelta(hours=4),
            by_session=True,
        )
        unread = _turn(db, room, started=now - timedelta(hours=4, minutes=30))
        _input(
            db,
            project,
            room,
            unread,
            executed_in=ended,
            at=now - timedelta(hours=4, minutes=29),
            echoed=False,
        )

        db.upgrade(AFTER)

        assert stopped_at(db, riding) is None
        assert stopped_at(db, unread) is None
        assert room_is_busy(db, room)
