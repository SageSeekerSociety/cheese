"""Three channel calls the platform's agents and composers make all day.

- Opening a room marks it read: the cursor moves to the room's newest block
  number. That number is asked of the room alone (a correlated ``max`` that
  walks ``ix_blocks_conversation_seq`` to one row); it used to join every
  block of the room and group them, which grows with the room.
- The composer asks whom a draft is for while it is typed. The route's door
  asks the caller's seat in the room and the answer reads the room's AI
  teammates; both come from one read of the room's roster.
- An AI teammate's control state is asked by its room, and the door is given
  the room row it already has instead of looking the room up again.
"""

import asyncio
import re

from sqlalchemy import text

from tests.integration.conftest import session_auth_headers
from tests.integration.test_topic_list_batches import _seed_populated_project
from tests.support.counting_session import counting_sql

_READ_CURSOR = re.compile(r"^INSERT INTO topic_read_states")
_ROSTER = re.compile(r"FROM topic_memberships WHERE topic_memberships\.topic_id")
_ROOM_OF = re.compile(r"UNION ALL SELECT threads\.id AS id, threads\.room_id")


def test_marking_a_room_read_asks_its_newest_number_by_index(client):
    _, rooms = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    with counting_sql(client) as statements:
        r = client.post(f"/topics/{rooms[0]}/read", json={}, headers=headers)
    assert r.status_code == 200, r.text
    cursor = [s for s in statements if _READ_CURSOR.search(s)]
    assert len(cursor) == 1, statements
    assert "GROUP BY" not in cursor[0], cursor[0]
    assert "LEFT OUTER JOIN blocks" not in cursor[0], cursor[0]

    # The cursor sits on the room's newest block.
    async def _cursor_and_newest():
        async with client.test_factory() as session:
            cursor = await session.scalar(
                text(
                    "SELECT last_read_seq FROM topic_read_states"
                    " WHERE topic_id = :t AND user_handle = 'alice'"
                ),
                {"t": rooms[0]},
            )
            newest = await session.scalar(
                text("SELECT max(seq) FROM blocks WHERE conversation_id = :t"),
                {"t": rooms[0]},
            )
            return cursor, newest

    cursor_seq, newest = asyncio.run(_cursor_and_newest())
    assert newest is not None and cursor_seq == newest


def test_the_composer_reads_the_room_roster_once(client):
    _, rooms = _seed_populated_project(client)
    with counting_sql(client) as statements:
        r = client.post(
            f"/topics/{rooms[0]}/messages/addressing",
            json={"content": "hi"},
            headers=session_auth_headers("alice"),
        )
    assert r.status_code == 200, r.text
    assert sum(1 for s in statements if _ROSTER.search(s)) == 1, statements


def test_an_agents_control_state_does_not_look_its_room_up_again(client):
    _, rooms = _seed_populated_project(client)
    with counting_sql(client) as statements:
        r = client.get(
            f"/topics/{rooms[0]}/agent/control", headers=session_auth_headers("alice")
        )
    assert r.status_code == 200, r.text
    assert not any(_ROOM_OF.search(s) for s in statements), statements
