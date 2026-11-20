"""Sending a message, the composer asking whom it is for, and marking a room
read read the people involved in as few statements as the answers need.

- A room's roster was read, and then which of its members are agents was a
  statement of its own; the roster is read with it now
  (`TopicMembershipRepository.list_for_topic`).
- Whether the project's owner is still on its team read the owner's account
  and then their team row; it is one statement now
  (`project_roles._handle_on_team`).
- Marking a room read read the room, then the reader's seat in it; the seat
  is read with the room now (`place_or_404(member=...)`).

A message said in a channel's main line also asks who the project's people
are, once, where nothing in the request read the roster already: the only
person in a project hands every such message to the AI teammate
(`turn.intake.rooms.only_person_in_project`). That read is counted below as
the one more statement it is.

The caller signs in with a token that carries their user id, as a signed-in
person's does, so nothing reads their own account to find it.
"""

import asyncio
import uuid

from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.common.auth import create_access_token
from app.domain.user.models import User
from tests.integration.conftest import join_project_team, post_message
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel


def _signed_in(client, handle: str) -> dict:
    async def user_id():
        async with client.test_factory() as db:
            return await db.scalar(select(User.id).where(User.username == handle))

    token = create_access_token(asyncio.run(user_id()), handle=handle)
    return {"Authorization": f"Bearer {token}"}


def _not_alone(client, project: str) -> None:
    """bob in the project: what alice says that names nobody starts no turn,
    whose reads would land among the ones counted here."""
    join_project_team(client, project, "bob")


def _statements(call) -> list[str]:
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", record)
    try:
        answer = call()
    finally:
        event.remove(Engine, "after_cursor_execute", record)
    assert answer.status_code == 200, answer.text
    return seen


def test_whom_a_draft_is_for_reads_the_agents_with_the_roster(client):
    project, _root, room = _channel(client)
    _not_alone(client, project)
    post_message(client, room, "alice", {"content": "hi"})
    headers = _signed_in(client, "alice")
    ask = lambda: client.post(  # noqa: E731
        f"/topics/{room}/messages/addressing",
        json={"content": "hi"},
        headers=headers,
    )
    ask()

    seen = _statements(ask)

    # Who is an agent is read with the roster, and the caller's own answer
    # with where they sit in the project (`project_roles._read_seat`): never
    # on its own.
    bindings = [s for s in seen if "agent_bindings" in s]
    assert bindings, seen
    assert all(
        "FROM topic_memberships" in s or "team_user_relation" in s for s in bindings
    ), bindings


def test_sending_reads_the_owners_team_row_with_their_account(client):
    project, _root, room = _channel(client)
    _not_alone(client, project)
    headers = _signed_in(client, "alice")
    send = lambda: client.post(  # noqa: E731
        f"/topics/{room}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "y"},
        headers=headers,
    )
    send()

    seen = _statements(send)

    assert [
        s
        for s in seen
        if s.startswith('SELECT "user".id') and "team_user_relation" not in s
    ] == [], seen
    teams = [s for s in seen if "team_user_relation" in s]
    # The owner's standing, with their account; and the project's people.
    assert len(teams) == 2, teams
    assert len([s for s in teams if 'JOIN "user"' in s]) == 1, teams


def test_marking_read_reads_the_room_with_the_readers_seat(client):
    project, _root, room = _channel(client)
    _not_alone(client, project)
    message = post_message(client, room, "alice", {"content": "hi"})
    headers = _signed_in(client, "alice")
    mark = lambda: client.post(  # noqa: E731
        f"/topics/{room}/read", json={"seq": message.get("seq", 1)}, headers=headers
    )
    mark()

    seen = _statements(mark)

    seats = [s for s in seen if "topic_memberships" in s]
    assert len(seats) == 1 and "FROM topics" in seats[0], seats


def test_whom_a_draft_is_for_reads_the_projects_agents_with_the_room(client):
    """Whom a draft would start a turn for asks which of the project's agents
    sits here; the agents come in the statement that reads the room."""
    project, _root, room = _channel(client)
    _not_alone(client, project)
    post_message(client, room, "alice", {"content": "hi"})
    headers = _signed_in(client, "alice")
    ask = lambda: client.post(  # noqa: E731
        f"/topics/{room}/messages/addressing",
        json={"content": "hi"},
        headers=headers,
    )
    ask()

    seen = _statements(ask)

    agents = [s for s in seen if "agent_instances" in s]
    assert len(agents) == 1 and "FROM topics" in agents[0], agents


def test_a_tasks_page_reads_the_task_with_its_room_once(client):
    """A task's id is not asked of `topics` first: the task, its room and the
    project come in one statement."""
    _, _root, room = _channel(client)
    headers = _signed_in(client, "alice")
    task = client.post(
        f"/topics/{room}/tasks", json={"title": "T"}, headers=headers
    ).json()["data"]["id"]
    ask = lambda: client.post(  # noqa: E731
        f"/topics/{task}/messages/addressing",
        json={"content": "hi"},
        headers=headers,
    )
    ask()

    seen = _statements(ask)

    rooms = [s for s in seen if s.startswith("SELECT topics.")]
    assert len(rooms) == 1 and "tasks.id" in rooms[0], rooms
    assert [s for s in seen if s.startswith("SELECT tasks.")] == [], seen


def test_a_members_standing_is_read_in_one_statement(client):
    """Someone who does not own the project: their account, team row,
    exclusion, roster row and role come in one statement."""
    project, _root, room = _channel(client)
    join_project_team(client, project, "bob")
    post_message(client, room, "alice", {"content": "hi"})
    headers = _signed_in(client, "bob")
    ask = lambda: client.post(  # noqa: E731
        f"/topics/{room}/messages/addressing",
        json={"content": "hi"},
        headers=headers,
    )
    ask()

    seen = _statements(ask)

    for table in ("project_member_exclusions", "project_members", "project_roles"):
        reads = [s for s in seen if table + " " in s or table + "." in s]
        standing = [s for s in reads if "team_user_relation" in s]
        assert len(standing) == 1, (table, reads)
        # The project's people read its members and exclusions once more.
        assert len(reads) == (1 if table == "project_roles" else 2), (
            table,
            reads,
        )
