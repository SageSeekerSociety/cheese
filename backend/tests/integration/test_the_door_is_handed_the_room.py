"""The room door is handed the room row the route already holds.

``PlaceResolver`` reads the room row for every route addressed by a conversation
(``Place.room``), so ``_actor_in_place`` hands it to ``authorize_topic``: the
conversation→room lookup (``room_of``) and the row read behind
``can_access_topic`` are not made a second time, and the roster is the only
question left.

Pinned here:

- a channel's header and its timeline make no conversation→room lookup at all —
  and the same request does make it when the door is handed only the id, so the
  count is a measuring stick and not a coincidence;
- the door answers the same from the row as from the id alone, for a private
  channel the caller is in and for one they are not;
- the row is what answers: an id naming another room does not change the answer
  (using that id where the row's id belongs would refuse a member);
- the timeline page is byte-for-byte what the id-only path returns.
"""

import re
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.api.auth.resolver import ActorResolver
from app.domain.room_task.place import PlaceResolver
from app.domain.topic.models import Topic
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
    session_token,
)

#: ``room_of``: the union over a room's tasks and 支线, aliased ``inside``. The
#: two arms are adjacent in it, so the pattern is the seam between them — a
#: notification read that correlates on both tables is not this lookup.
# Which room a conversation is in: asked on its own (`conversations.room_of`)
# or inside the statement that reads that room's row
# (`TopicRepository.room_of_conversation`).
_ROOM_LOOKUP = re.compile(
    r"FROM tasks\s+UNION ALL SELECT threads\.id"
    r"|WHERE topics\.id = coalesce\(\(SELECT tasks\.room_id",
    re.I,
)


def _room(client) -> str:
    """A channel with one message in it, made and read by alice."""
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "一个频道"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    tid = r.json()["data"]["id"]
    post_message(client, tid, "alice", {"content": "这儿有人吗"})
    return tid


def _statements_while(client, request):
    """Run ``request`` and return its response with every statement it ran."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = request()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return response, statements


def _room_lookups(statements) -> int:
    return sum(1 for statement in statements if _ROOM_LOOKUP.search(statement))


@contextmanager
def _door_handed_only_the_id():
    """The door as it was before the change: the id alone, no room row, so it
    resolves the conversation's room itself — and resolving the place does not
    leave the room behind for it either (`PlaceResolver.conversation`)."""
    original = ActorResolver.authorize_topic

    async def without_the_room(
        self, actor, *, project_id, topic_id=None, room=None, enforce=False
    ):
        if topic_id is None and room is not None:
            topic_id = room.id
        return await original(
            self, actor, project_id=project_id, topic_id=topic_id, enforce=enforce
        )

    with (
        patch.object(ActorResolver, "authorize_topic", without_the_room),
        patch.object(PlaceResolver, "conversation", PlaceResolver._conversation),
    ):
        yield


@pytest.mark.parametrize("path", ["/topics/{tid}", "/topics/{tid}/blocks?limit=50"])
def test_a_channel_read_makes_no_conversation_lookup(client, path):
    tid = _room(client)
    url = path.format(tid=tid)
    read = lambda: client.get(url, headers=session_auth_headers("alice"))  # noqa: E731

    response, statements = _statements_while(client, read)
    assert response.status_code == 200, response.text
    assert _room_lookups(statements) == 0, [
        s for s in statements if _ROOM_LOOKUP.search(s)
    ]

    # The ruler bites: the pre-change door (id only) does look the room up on
    # the very same request, so "0" above measures the change, not a blindspot.
    with _door_handed_only_the_id():
        response, statements = _statements_while(client, read)
    assert response.status_code == 200, response.text
    assert _room_lookups(statements) >= 1


def test_a_refused_read_makes_no_conversation_lookup(client):
    """The refusal path reads the room row it was handed too: carol is on the
    team, not in the private channel, and is told the channel does not exist."""
    p, a, _b = _private_channels(client)
    response, statements = _statements_while(
        client,
        lambda: client.get(f"/topics/{a}", headers=session_auth_headers("carol")),
    )
    assert response.status_code == 404, response.text
    assert _room_lookups(statements) == 0


def _private_channels(client):
    """alice's project with private channels A and B; bob is in A only.

    carol is on the team (a project member) and in neither channel."""
    for who in ("alice", "bob", "carol"):
        seed_user(client, who)
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    for handle in ("bob", "carol"):
        join_project_team(client, p["id"], handle)

    def make() -> str:
        r = client.post(
            "/topics",
            json={"project_id": p["id"], "title": "机密", "members_only": True},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["id"]

    a, b = make(), make()
    added = client.post(
        f"/topics/{a}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text
    return p, a, b


def _door(client, *, who: str, project_id: str, topic_id: str, room: str | None = None):
    """What ``authorize_topic`` answers, with or without the room row."""

    async def _ask() -> str:
        async with client.test_request_factory() as session:
            row = await session.get(Topic, uuid.UUID(room)) if room else None
            resolver = ActorResolver(
                session=session, bearer=session_token(who), cheese_token=""
            )
            actor = await resolver.resolve(
                topic_id=uuid.UUID(topic_id), project_id=uuid.UUID(project_id)
            )
            try:
                await resolver.authorize_topic(
                    actor,
                    project_id=uuid.UUID(project_id),
                    topic_id=uuid.UUID(topic_id),
                    room=row,
                )
            except Exception as exc:  # the answer IS the class
                return type(exc).__name__
            return "allowed"

    return client.portal.call(_ask)


def test_the_door_answers_the_same_with_the_row_as_from_the_id(client):
    p, a, b = _private_channels(client)

    for topic_id in (a, b):
        by_id = _door(client, who="bob", project_id=p["id"], topic_id=topic_id)
        by_row = _door(
            client, who="bob", project_id=p["id"], topic_id=topic_id, room=topic_id
        )
        assert by_row == by_id

    assert _door(client, who="bob", project_id=p["id"], topic_id=a) == "allowed"
    assert _door(client, who="bob", project_id=p["id"], topic_id=b) == "NotFoundError"


def test_the_row_handed_to_the_door_is_the_room_it_answers(client):
    """The wrong shortcut — the id in front of the caller used as the room —
    is refused here: bob is in A, so an id naming B beside A's row must still
    be answered by A."""
    p, a, b = _private_channels(client)

    assert _door(client, who="bob", project_id=p["id"], topic_id=b, room=a) == "allowed"
    # Without the row the id alone decides, and B is not bob's: the two are
    # different questions, which is why the row must win when it is given.
    assert _door(client, who="bob", project_id=p["id"], topic_id=b) == "NotFoundError"


def test_the_timeline_page_is_unchanged_when_the_door_is_handed_the_room(client):
    tid = _room(client)
    post_message(client, tid, "alice", {"content": "再来一条"})
    url = f"/topics/{tid}/blocks?limit=50"
    read = lambda: client.get(url, headers=session_auth_headers("alice"))  # noqa: E731

    with_the_room = read()
    assert with_the_room.status_code == 200, with_the_room.text
    with _door_handed_only_the_id():
        without_the_room = read()
    assert without_the_room.status_code == 200, without_the_room.text

    assert with_the_room.content == without_the_room.content
