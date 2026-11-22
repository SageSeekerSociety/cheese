"""A person already working in an environment of a conversation keeps working
there when the conversation comes to point elsewhere; a person who works in
none goes where the conversation's work is."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.agent_session import attachment as attachments
from app.domain.environment import services as environment_rows
from app.domain.topic.models import Topic
from app.domain.user.models import User
from tests.integration.conftest import post_project, session_auth_headers

pytestmark = pytest.mark.anyio

STANDARD = {"kind": "sandbox", "spec": "standard"}


async def test_a_person_is_not_moved_and_a_newcomer_goes_where_the_work_is(client):
    project = post_project(client, json={"name": "Stays"}, owner="alice").json()["data"]
    room_id = uuid.UUID(
        client.post(
            "/topics",
            json={"project_id": project["id"], "title": "Room"},
            headers=session_auth_headers("alice"),
        ).json()["data"]["id"]
    )
    async with client.test_factory() as db:
        topic = await db.get(Topic, room_id)
        where = environment_rows.Where(
            topic_id=room_id,
            project_id=uuid.UUID(project["id"]),
            generation=str(topic.resource_id or room_id),
            choice=STANDARD,
        )
        alice = await db.scalar(select(User.id).where(User.username == "alice"))
        settle = {"conversation_id": room_id, "default": STANDARD, "where": where}

        first = await attachments.settle_person(db, user_id=alice, **settle)
        # The conversation's work moves to another environment.
        elsewhere = environment_rows.new_environment(db, where)
        await db.flush()
        pointer = await environment_rows.pointer(db, room_id)
        pointer.environment_id = elsewhere.id
        await db.flush()

        again = await attachments.settle_person(db, user_id=alice, **settle)
        assert (again.id, again.environment_id) == (first.id, first.environment_id)

        bob = await db.scalar(select(User.id).where(User.username == "bob"))
        if bob is None:
            bob = User(
                username="bob",
                email="bob@example.test",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(bob)
            await db.flush()
            bob = bob.id
        newcomer = await attachments.settle_person(db, user_id=bob, **settle)
        assert newcomer.environment_id == elsewhere.id
        await db.rollback()
