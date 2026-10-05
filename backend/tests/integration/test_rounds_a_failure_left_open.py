"""The rounds a failure left open stop holding their room's seat (cffd329c35e3).

The bug: a round the API refuses ends without the completion stamp, and the
runner's own terminal stamp was skipped whenever it held a harness background
task at that instant (#2570). The inputs the round read stayed unfinished,
the seat read them as outstanding holds, and deferred every later message — the
room went silent and never came back.

The migration runs on the database of its own revision: the scenario is seeded
with raw SQL at the revision before it, the migration is applied, and what is
read back is data. A seat holds while an input it was sent is neither completed
nor terminated, or while the round that owns it is still open, so the round's
end and the input's terminal stamp are what is checked.
"""

import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    room_is_busy,
    seed_block,
    seed_room,
    seed_task,
    seed_turn,
    stopped_at,
)

BEFORE = "7c3e5a9d1f20"
AFTER = "cffd329c35e3"

SEAT = "cheese-0123456789ab"

#: What the platform writes under a round's id when it broke.
BROKE = {"event_type": "turn_failed", "severity": "error"}


def _turn(
    db,
    project,
    room,
    *,
    started,
    stopped=None,
    delivered=True,
    task=None,
    said=(),
    broke_at=None,
):
    """One round on ``room``'s seat and a block under its id for each
    ``(at, author)`` in ``said``. ``broke_at`` adds the platform event that
    records the round ended in an error — the evidence the migration reads."""
    turn = seed_turn(
        db,
        room,
        started=started,
        stopped=stopped,
        delivered=delivered,
        author=SEAT,
        seat=SEAT,
        task=task,
    )
    for at, by in said:
        seed_block(db, project, room, turn=turn, at=at, author=by)
    if broke_at is not None:
        seed_block(
            db, project, room, turn=turn, at=broke_at, author="system", meta=BROKE
        )
    return turn


def _input(
    db: ReplayDatabase,
    project,
    room,
    *,
    work,
    registered,
    echoed_at=None,
    opened=True,
):
    """One input row for ``work``: ``echoed_at`` is the session's statement that
    it read the input, and an input that opened its own work is the one whose
    id names it (``input_id = work_id``)."""
    input_id = work if opened else uuid.uuid4()
    db.execute(
        "INSERT INTO native_inputs (id, project_id, topic_id, recipient_handle,"
        " harness, native_session_id, input_id, work_id, execution_work_id,"
        " held_block_ids, released_block_ids, block_ids, seen_block_ids,"
        " registered_at, echoed_at)"
        " VALUES ($1, $2, $3, $4, 'claude_code', $5, $6, $7, $8, '[]', '[]',"
        " '[]', '[]', $9, $10)",
        uuid.uuid4(),
        project,
        room,
        SEAT,
        str(uuid.uuid4()),
        input_id,
        work,
        work if echoed_at is not None else None,
        registered,
        echoed_at,
    )
    return input_id


def _unfinished_inputs(db: ReplayDatabase, room) -> int:
    """Inputs the seat is still waiting on: neither completed nor terminated."""
    return db.fetchval(
        "SELECT count(*) FROM native_inputs WHERE topic_id = $1"
        " AND completed_at IS NULL AND terminated_at IS NULL",
        room,
    )


def _input_state(db: ReplayDatabase, input_id) -> tuple:
    row = db.fetchrow(
        "SELECT completed_at, terminated_at, termination"
        " FROM native_inputs WHERE input_id = $1",
        input_id,
    )
    return tuple(row)


def test_a_round_recorded_as_failed_stops_holding_its_seat():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        last_word = now - timedelta(hours=15)
        broke_at = now - timedelta(hours=14)
        round_ = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            said=((last_word, "alice"),),
            broke_at=broke_at,
        )
        read = _input(
            db,
            project,
            room,
            work=round_,
            registered=now - timedelta(hours=20),
            echoed_at=now - timedelta(hours=19),
        )

        assert room_is_busy(db, room)
        assert _unfinished_inputs(db, room) == 1

        db.upgrade(AFTER)

        assert not room_is_busy(db, room)
        assert _unfinished_inputs(db, room) == 0
        # The round ends at the last thing it wrote — the failure event itself.
        assert stopped_at(db, round_) == broke_at
        # The input it read is settled as a terminal outcome, never as a completion:
        # whether the answer inside it was taken is still unknown.
        completed, terminated, termination = _input_state(db, read)
        assert completed is None
        assert terminated == broke_at
        assert termination == "is_error"


def test_an_input_the_round_never_read_is_settled_by_the_round_ending():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        now = datetime.now(UTC)
        round_ = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            broke_at=now - timedelta(hours=14),
        )
        # Nothing says the session ever took it: the round ending is all it waits for.
        never_read = _input(
            db, project, room, work=round_, registered=now - timedelta(hours=20)
        )

        assert room_is_busy(db, room)

        db.upgrade(AFTER)

        # The input is left as it was; what frees the seat is its round's end.
        assert not room_is_busy(db, room)
        assert stopped_at(db, round_) is not None
        assert _input_state(db, never_read) == (None, None, None)


def test_rounds_without_that_evidence_keep_their_seat():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        now = datetime.now(UTC)

        # Quiet for hours, but nothing recorded that it broke.
        silent = _turn(db, project, room, started=now - timedelta(hours=20))
        silent_input = _input(
            db,
            project,
            room,
            work=silent,
            registered=now - timedelta(hours=20),
            echoed_at=now - timedelta(hours=19),
        )
        # Broke hours ago and wrote an hour ago: its session is still on it.
        working = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            said=((now - timedelta(hours=1), "alice"),),
            broke_at=now - timedelta(hours=14),
        )
        working_input = _input(
            db,
            project,
            room,
            work=working,
            registered=now - timedelta(hours=20),
            echoed_at=now - timedelta(hours=19),
        )
        # On a task's line: the task's own machinery settles it.
        on_a_task = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=20),
            task=task,
            broke_at=now - timedelta(hours=14),
        )
        task_input = _input(
            db,
            project,
            room,
            work=on_a_task,
            registered=now - timedelta(hours=20),
            echoed_at=now - timedelta(hours=19),
        )
        # Never reached its session: the orphan sweep's to re-send.
        undelivered = _turn(
            db,
            project,
            room,
            started=now - timedelta(hours=30),
            delivered=False,
            broke_at=now - timedelta(hours=14),
        )
        undelivered_input = _input(
            db,
            project,
            room,
            work=undelivered,
            registered=now - timedelta(hours=30),
            echoed_at=now - timedelta(hours=29),
        )

        db.upgrade(AFTER)

        assert stopped_at(db, silent) is None
        assert stopped_at(db, working) is None
        assert stopped_at(db, on_a_task) is None
        assert stopped_at(db, undelivered) is None
        for input_id in (silent_input, working_input, task_input, undelivered_input):
            assert _input_state(db, input_id) == (None, None, None)
        assert room_is_busy(db, room)
        assert _unfinished_inputs(db, room) == 4
