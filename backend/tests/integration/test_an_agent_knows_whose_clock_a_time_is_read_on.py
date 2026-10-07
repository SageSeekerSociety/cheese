"""An agent writes times for people, and they read them on their own clocks.

The times an agent reads come from the API as UTC instants. Unless it knows
where its readers are, the clock time it copies into a task document is the UTC
one: a message sent at 00:52 in Los Angeles came back in the document as
「07:52」 while the chat beside it said 00:52. The room roster an agent is
handed carries each person's reported zone, so the agent has what it needs to
write a time the reader will recognise.
"""

import uuid

from sqlalchemy import update

from app.domain.agent.room.turn import room_roster
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import join_project_team, post_project


def _roster(client, project_id: str, topic_id: str) -> dict[str, dict]:
    async def read() -> dict[str, dict]:
        async with client.test_factory() as session:
            topic = await session.get(Topic, uuid.UUID(topic_id))
            rows = await room_roster(session, uuid.UUID(project_id), topic)
            return {row["handle"]: row for row in rows}

    return client.portal.call(read)


def _report_zone(client, handle: str, zone: str) -> None:
    async def write() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(User).where(User.username == handle).values(timezone=zone)
            )
            await session.commit()

    client.portal.call(write)


def test_the_room_roster_says_where_each_person_reads_their_clock(client):
    data = post_project(client, {"name": "Clocks"}, owner="alice").json()["data"]
    join_project_team(client, data["id"], "bob")
    _report_zone(client, "alice", "America/Los_Angeles")

    rows = _roster(client, data["id"], data["root_topic_id"])

    assert rows["alice"]["timezone"] == "America/Los_Angeles"
    # Someone whose browser never reported a zone is not given a guessed one.
    assert "timezone" not in rows["bob"]
