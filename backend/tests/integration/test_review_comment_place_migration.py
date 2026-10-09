"""What migration 4195c0bc87ad does to the comments already written.

Every comment written before it is on lines of a text file, and after it every
comment names where it points: those already written say their lines, one line
or a span, and none is left without a place.
"""

import uuid

import pytest

from tests.integration.migration_replay import ReplayDatabase, database_at

BEFORE = "c5d2e8a1f4b7"
AFTER = "4195c0bc87ad"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _comment(db: ReplayDatabase, start: int, end: int) -> uuid.UUID:
    comment = uuid.uuid4()

    async def work(conn):
        # The comment's task is beside the point here; its foreign key is not.
        await conn.execute("SET session_replication_role = replica")
        await conn.execute(
            "INSERT INTO review_comments (id, task_id, author_handle, path,"
            " line_start, line_end, line_text, body, state, created_at, updated_at)"
            " VALUES ($1, $2, 'alice', 'app.py', $3, $4, 'x', 'fix', 'sent', now(),"
            " now())",
            comment,
            uuid.uuid4(),
            start,
            end,
        )

    db.run(work)
    return comment


def test_comments_already_written_name_their_lines(db):
    one = _comment(db, 12, 12)
    span = _comment(db, 40, 44)

    db.upgrade(AFTER)

    places = {
        row["id"]: row["place"]
        for row in db.fetch("SELECT id, place FROM review_comments")
    }
    assert places == {one: "L12", span: "L40-L44"}
