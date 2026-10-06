"""Turns of messages answered inside another turn are ended (migration 91616b2af5b6).

A message that reached a session while another turn held its seat was answered
inside that turn, and its own turn was never given an end. The room then stayed
busy for good: an environment change was refused as if the agent were working.

The migration runs on the database of its own revision: the scenario is seeded
with raw SQL at the revision before it, the migration is applied, and what is
read back is data. A room is busy exactly while one of its turns has no
``stopped_at``, so that is what is checked, along with whether each turn is
still running.
"""

from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    database_at,
    room_is_busy,
    seed_block,
    seed_room,
    seed_turn,
    stopped_at,
)

BEFORE = "da05dacf50ae"
AFTER = "91616b2af5b6"

SEAT = "cheese-0123456789ab"
OTHER_SEAT = "cheese-ba9876543210"


def _turn(db, project, room, *, said_at=(), seat=SEAT, author="alice", **kwargs):
    """One turn in ``room``, and a block written under its id at each ``said_at``."""
    turn = seed_turn(db, room, seat=seat, author=author, **kwargs)
    for at in said_at:
        seed_block(db, project, room, turn=turn, at=at, author=seat)
    return turn


def test_a_room_held_busy_by_messages_answered_mid_turn_is_free_again():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        host_end = now - timedelta(hours=28)
        _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=30),
            stopped=host_end,
        )
        message = _turn(db, project, room, started=now - timedelta(hours=29))
        nudge = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=29, minutes=-5),
            author="system",
        )

        assert room_is_busy(db, room)

        db.upgrade(AFTER)

        assert not room_is_busy(db, room)
        # They end when the turn that answered them ended.
        assert stopped_at(db, message) == host_end
        assert stopped_at(db, nudge) == host_end


def test_a_turn_that_may_still_be_running_is_left_running():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # Sent minutes ago while another turn was running: too recent to tell
        # whether it was answered there or is running on its own now.
        _turn(
            db,
            project,
            room,
            started=now - timedelta(minutes=40),
            stopped=now - timedelta(minutes=20),
        )
        recent = _turn(db, project, room, started=now - timedelta(minutes=30))
        # Sent hours ago while another turn was running, but it is still writing:
        # it is the turn the session is working on.
        _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=10),
            stopped=now - timedelta(hours=9),
        )
        working = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=9, minutes=30),
            said_at=(now - timedelta(minutes=10),),
        )

        db.upgrade(AFTER)

        assert stopped_at(db, recent) is None
        assert stopped_at(db, working) is None
        assert room_is_busy(db, room)


def test_a_message_answered_inside_a_turn_that_never_ended_is_ended():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # A turn that worked for hours and then went quiet without an end.
        host = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            said_at=(now - timedelta(hours=19), now - timedelta(hours=12)),
        )
        answered = _turn(
            db, project, room, started=now - timedelta(hours=19, minutes=30)
        )
        # Sent after that turn had gone quiet: nothing shows it was answered there.
        after_quiet = _turn(db, project, room, started=now - timedelta(hours=11))

        before = datetime.now(UTC)
        db.upgrade(AFTER)

        ended = stopped_at(db, answered)
        assert ended is not None and ended >= before - timedelta(seconds=5)
        assert stopped_at(db, host) is None
        assert stopped_at(db, after_quiet) is None
        assert room_is_busy(db, room)


def test_another_seat_or_an_undelivered_turn_is_not_ended():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=30),
            stopped=now - timedelta(hours=28),
        )
        # Another teammate's turn, sent while the first one's was running.
        other_seat = _turn(
            db, project, room, started=now - timedelta(hours=29), seat=OTHER_SEAT
        )
        # Never reached the session: the orphan sweep's to re-send.
        undelivered = _turn(
            db, project, room, started=now - timedelta(hours=29), delivered=False
        )

        db.upgrade(AFTER)

        assert stopped_at(db, other_seat) is None
        assert stopped_at(db, undelivered) is None
        assert room_is_busy(db, room)
