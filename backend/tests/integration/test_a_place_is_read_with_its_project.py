"""A room is resolved together with its project.

Nearly every room route resolves the room first (`PlaceResolver`) and asks
about its project next: whether it is archived, before any write, and who may
do what in it. The project was a second statement on every one of them.
"""

from sqlalchemy import event
from sqlalchemy.engine import Engine

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
