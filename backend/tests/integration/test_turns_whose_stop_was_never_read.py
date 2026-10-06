"""Turns whose Stop the room's reader never got past are ended (migration 80aabe850e1e).

A room's reader stopped at a turn's result and never read on, so that turn and
the ones after it on the same seat were never given an end, and the room stayed
busy: an environment change was refused as if the agent were working.

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

BEFORE = "91616b2af5b6"
AFTER = "80aabe850e1e"

SEAT = "cheese"
AGENT = "cheese-0123456789ab"
OTHER_AGENT = "cheese-ba9876543210"


def _turn(
    db,
    project,
    room,
    *,
    started,
    stopped=None,
    delivered=True,
    author="alice",
    self_started=False,
    said=(),
):
    """One turn on ``room``'s seat, and a block under its id for each
    ``(at, author)`` in ``said``. A self-started turn is what a session opens
    for work nobody fed it: authored by its agent, no prompt to re-send."""
    turn = seed_turn(
        db,
        room,
        started=started,
        stopped=stopped,
        delivered=delivered,
        author=AGENT if self_started else author,
        content="" if self_started else "hello",
        resendable=not self_started,
        seat=SEAT,
    )
    for at, by in said:
        seed_block(db, project, room, turn=turn, at=at, author=by)
    return turn


def test_a_room_whose_reader_stopped_at_a_turn_is_free_again():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # The session's own turn: it said its last word, and its Stop was never read.
        last_word = now - timedelta(hours=14)
        own = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            self_started=True,
            said=((now - timedelta(hours=19), AGENT), (last_word, AGENT)),
        )
        # A message it answered, whose Stop was not read either; the seat went on
        # to a later turn that wrote under its own id.
        answered_at = now - timedelta(hours=12)
        answered = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=13),
            said=((answered_at, AGENT),),
        )
        _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=11),
            stopped=now - timedelta(hours=10),
            said=((now - timedelta(hours=10, minutes=30), AGENT),),
        )

        assert room_is_busy(db, room)

        db.upgrade(AFTER)

        assert not room_is_busy(db, room)
        # Each ends at the last thing it wrote.
        assert stopped_at(db, own) == last_word
        assert stopped_at(db, answered) == answered_at


def test_a_turn_the_session_may_still_be_on_is_left_running():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # Its agent wrote in the room an hour ago: the session is still on it.
        # Another agent's block on its id is not its own and proves nothing.
        working = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            self_started=True,
            said=(
                (now - timedelta(hours=1), AGENT),
                (now - timedelta(hours=2), OTHER_AGENT),
            ),
        )
        # Quiet for hours, but nothing on its seat came after it.
        last_on_seat = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=9),
            said=((now - timedelta(hours=8), AGENT),),
        )
        # Sent recently.
        recent = _turn(db, project, room, started=now - timedelta(hours=2))
        # Never reached its session: the orphan sweep's to re-send.
        undelivered = _turn(
            db, project, room, started=now - timedelta(hours=30), delivered=False
        )

        db.upgrade(AFTER)

        assert stopped_at(db, working) is None
        assert stopped_at(db, last_on_seat) is None
        assert stopped_at(db, recent) is None
        assert stopped_at(db, undelivered) is None
        assert room_is_busy(db, room)


def test_another_agents_block_on_its_id_does_not_keep_it_open():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        # Its own last word was long ago; an hour ago another agent's message was
        # attached to it because it was the room's newest open turn.
        last_word = now - timedelta(hours=13)
        own = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=14),
            self_started=True,
            said=((last_word, AGENT), (now - timedelta(hours=1), OTHER_AGENT)),
        )

        db.upgrade(AFTER)

        assert stopped_at(db, own) == last_word
        assert not room_is_busy(db, room)
