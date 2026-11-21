"""c41e7b9d2f05..e63a9b1d4f27: an attachment names the conversation its
attacher works in, and may be a person's.

- an agent already attached works in its session's conversation;
- an agent the previous release attaches while this one is being deployed
  works in its session's conversation too, though that release names none;
- an attachment is an agent's or a person's, never both and never neither;
- a person is attached to an environment at most once.
"""

import json
import uuid

import asyncpg
import pytest

from tests.integration.migration_replay import (
    ReplayDatabase,
    database_at,
    seed_room,
    seed_task,
)

BEFORE = "ac5501d67db3"
AFTER = "e63a9b1d4f27"


def _session(db: ReplayDatabase, conversation: uuid.UUID) -> uuid.UUID:
    row = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_sessions (id, conversation_id, agent_handle, harness,"
        " created_at, updated_at) VALUES ($1, $2, 'cheese', 'claude-code',"
        " now(), now())",
        row,
        conversation,
    )
    return row


def _environment(db: ReplayDatabase, conversation: uuid.UUID) -> uuid.UUID:
    environment = uuid.uuid4()
    db.execute(
        "INSERT INTO environments (id, topic_id, resource_id, room_resource_id,"
        " choice, created_at, updated_at) VALUES ($1, $2, $3, $4, $5::jsonb,"
        " now(), now())",
        environment,
        conversation,
        str(uuid.uuid4()),
        str(conversation),
        json.dumps({"kind": "sandbox", "spec": "standard"}),
    )
    return environment


def _attach_as_the_previous_release(db, environment, session) -> None:
    db.execute(
        "INSERT INTO environment_attachments (id, environment_id, session_id,"
        " generation, acted_at) VALUES ($1, $2, $1, 'g', now())",
        session,
        environment,
    )


def _conversation_of(db: ReplayDatabase, attachment: uuid.UUID):
    return db.fetchval(
        "SELECT conversation_id FROM environment_attachments WHERE id = $1",
        attachment,
    )


def _user(db: ReplayDatabase) -> int:
    return db.fetchval(
        'INSERT INTO "user" (username, email, created_at, updated_at)'
        " VALUES ($1, $2, now(), now()) RETURNING id",
        f"p{uuid.uuid4().hex[:8]}",
        f"{uuid.uuid4().hex[:8]}@example.com",
    )


def test_every_attachment_names_the_conversation_its_attacher_works_in():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        task = seed_task(db, project, room)
        environment = _environment(db, task)
        already = _session(db, task)
        _attach_as_the_previous_release(db, environment, already)

        db.upgrade(AFTER)

        assert _conversation_of(db, already) == task
        later = _session(db, room)
        _attach_as_the_previous_release(db, _environment(db, room), later)
        assert _conversation_of(db, later) == room


def test_an_attachment_is_one_agent_s_or_one_person_s():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        environment = _environment(db, room)
        db.upgrade(AFTER)
        person = _user(db)
        session = _session(db, room)

        def attach(*, session_id, user_id):
            db.execute(
                "INSERT INTO environment_attachments (id, environment_id,"
                " session_id, user_id, conversation_id, generation, acted_at)"
                " VALUES ($1, $2, $3, $4, $5, 'g', now())",
                uuid.uuid4(),
                environment,
                session_id,
                user_id,
                room,
            )

        attach(session_id=None, user_id=person)
        with pytest.raises(asyncpg.UniqueViolationError):
            attach(session_id=None, user_id=person)
        with pytest.raises(asyncpg.CheckViolationError):
            attach(session_id=session, user_id=person)
        with pytest.raises(asyncpg.CheckViolationError):
            attach(session_id=None, user_id=None)
