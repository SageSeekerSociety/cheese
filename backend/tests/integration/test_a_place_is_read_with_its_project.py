"""A room is resolved together with its project.

Nearly every room route resolves the room first (`PlaceResolver`) and asks
about its project next: whether it is archived, before any write, and who may
do what in it. The project was a second statement on every one of them.
"""

import asyncio
import uuid

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.domain.room_task.place import PlaceResolver
from app.domain.topic.models import Topic
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel


def test_marking_a_room_read_reads_its_project_with_the_room(client):
    _, _root, room = _channel(client)
    message = post_message(client, room, "alice", {"content": "hi"})
    mark = lambda: client.post(  # noqa: E731
        f"/topics/{room}/read",
        json={"seq": message.get("seq", 1)},
        headers=session_auth_headers("alice"),
    )
    assert mark().status_code == 200
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", record)
    try:
        assert mark().status_code == 200
    finally:
        event.remove(Engine, "after_cursor_execute", record)

    projects = [s for s in seen if "FROM projects" in s or "JOIN projects" in s]
    assert len(projects) == 1, projects
    assert "FROM topics" in projects[0], projects


def test_a_room_expired_by_a_rollback_is_read_afresh(client):
    """A rollback expires the room the session still holds. Resolving it again
    reads it, instead of handing back a row whose columns would have to load
    lazily, which an async session cannot do."""
    _, _root, room = _channel(client)

    async def _run():
        async with client.test_factory() as session:
            held = await session.get(Topic, uuid.UUID(room))
            assert held is not None
            await session.rollback()
            place = await PlaceResolver(session).resolve(uuid.UUID(room))
            assert place is not None and place.room is held
            name = place.room.title
            await session.rollback()
        return name

    assert asyncio.run(_run())
