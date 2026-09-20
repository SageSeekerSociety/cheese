"""The device-connection owner keeps working when the schema moves under it.

An app release deliberately leaves that process running its old image, and the
same release runs the migrations — so old code against a migrated schema is its
normal state. On 2026-09-19 a dropped column (`topics.agent_instance_id`) made
every room command it served fail for three hours, 1218 times, because the code
loaded whole ORM models: a model lists every column the table had when that
image was built.

These tests drop a column the owner has no business needing and then ask it its
questions. A read that names its columns does not notice; a read that loads a
model raises `UndefinedColumnError`, which is exactly the incident.
"""

import uuid

import pytest
from sqlalchemy import text

from app.domain.device import owner_reads


async def _project(db_session, handle: str = "alice") -> uuid.UUID:
    project_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO projects"
            " (id, name, owner_handle, ai_mode, summary, settings,"
            " created_at, updated_at)"
            " VALUES (:id, :name, :owner, 'off', '', '{}', now(), now())"
        ),
        {"id": project_id, "name": "Owner reads", "owner": handle},
    )
    return project_id


async def _room(db_session, project_id: uuid.UUID) -> uuid.UUID:
    room_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO topics"
            " (id, project_id, title, kind, status, is_private,"
            " created_at, updated_at)"
            " VALUES (:id, :project, :title, 'room', 'active', false,"
            " now(), now())"
        ),
        {"id": room_id, "project": project_id, "title": "A room"},
    )
    return room_id


async def _without_column(db_session, table: str, column: str) -> None:
    """Drop a column for the rest of this test, the way a migration would.

    The test's outer transaction is rolled back afterwards, so the column comes
    back — and the ORM model still has it either way, which is the point.
    """
    await db_session.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def test_a_room_can_be_placed_when_a_column_it_never_used_is_gone(db_session, _portal):
    async def ask():
        project_id = await _project(db_session)
        room_id = await _room(db_session, project_id)
        await _without_column(db_session, "topics", "title")
        return project_id, await owner_reads.place(db_session, room_id)

    project_id, place = _portal.call(ask)
    assert place is not None and place.project_id == project_id


def test_a_project_can_be_checked_when_a_column_it_never_used_is_gone(
    db_session, _portal
):
    async def ask():
        project_id = await _project(db_session, handle="alice")
        await _without_column(db_session, "projects", "last_heartbeat_at")
        return (
            await owner_reads.project_exists(db_session, project_id),
            await owner_reads.project_owner(db_session, project_id),
        )

    assert _portal.call(ask) == (True, "alice")


def test_a_place_that_is_neither_a_room_nor_a_task_is_absent(db_session, _portal):
    async def ask():
        return await owner_reads.place(db_session, uuid.uuid4())

    assert _portal.call(ask) is None


def test_loading_the_whole_room_is_what_the_incident_was(db_session, _portal):
    """The same question asked the old way, to show the difference is real and
    not the test's arrangement: a model load against the dropped column raises."""
    from sqlalchemy.exc import ProgrammingError

    from app.domain.topic.repositories import TopicRepository

    async def ask_the_old_way():
        project_id = await _project(db_session)
        room_id = await _room(db_session, project_id)
        await _without_column(db_session, "topics", "title")
        return await TopicRepository(db_session).get(room_id)

    with pytest.raises(ProgrammingError, match="title"):
        _portal.call(ask_the_old_way)
