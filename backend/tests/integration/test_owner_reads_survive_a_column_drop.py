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
from tests.integration.conftest import a_team, session_token


async def _project(db_session, handle: str = "alice") -> uuid.UUID:
    project_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO projects"
            " (id, name, owner_handle, team_id, ai_mode, summary, settings,"
            " created_at, updated_at)"
            " VALUES (:id, :name, :owner, :team, 'off', '', '{}', now(), now())"
        ),
        {
            "id": project_id,
            "name": "Owner reads",
            "owner": handle,
            "team": await a_team(db_session),
        },
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


def test_a_project_can_be_checked_when_a_column_it_never_used_is_gone(
    db_session, _portal
):
    async def ask():
        project_id = await _project(db_session, handle="alice")
        await _without_column(db_session, "projects", "last_heartbeat_at")
        return await owner_reads.project_owner(db_session, project_id)

    assert _portal.call(ask) == "alice"


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


def test_device_auth_survives_unrelated_column_drops(db_session, _portal):
    from tests.integration.test_archive_retires_storage import _seed_device

    async def ask():
        project = await _project(db_session)
        await _seed_device(db_session, "allowed", project_id=project)
        await _without_column(db_session, "device", "cloud_control_private")
        await _without_column(db_session, "device", "visibility")
        await _without_column(db_session, "hosted_device", "owner_user_id")
        identity = await owner_reads.device_for_token(db_session, "tok-allowed")
        assert identity == owner_reads.DeviceIdentity("allowed", "allowed")
        assert await owner_reads.device_for_token(db_session, "wrong") is None
        assert await owner_reads.device_for_token(db_session, "") is None

    _portal.call(ask)


def test_device_can_connect_after_an_unrelated_column_is_dropped(
    api_client, db_session, _portal, monkeypatch
):
    from app.core.config import settings
    from tests.integration.test_archive_retires_storage import _seed_device

    async def prepare():
        await _seed_device(db_session, "connect-after-migration")
        await _without_column(db_session, "device", "cloud_control_private")

    _portal.call(prepare)
    monkeypatch.setattr(settings, "device_connection_owner", True)
    with api_client.websocket_connect(
        "/connector/agent?token=tok-connect-after-migration"
    ) as ws:
        assert ws.receive_json()["t"] == "welcome"


@pytest.mark.parametrize("membership", ["project", "topic"])
def test_viewer_authorization_survives_unrelated_membership_column_drops(
    db_session, _portal, membership
):
    from types import SimpleNamespace

    from app.api.routes.connector import _may_view_screen
    from app.domain.project.models import ProjectMember
    from app.domain.topic.models import TopicMembership

    async def ask():
        project = await _project(db_session, handle="owner")
        room = await _room(db_session, project)
        if membership == "project":
            db_session.add(ProjectMember(project_id=project, user_handle="viewer"))
        else:
            db_session.add(TopicMembership(topic_id=room, member_handle="viewer"))
        await db_session.flush()
        await _without_column(db_session, "topic_memberships", "role")
        screen = SimpleNamespace(project_id=project, topic_id=room)
        assert await _may_view_screen(db_session, screen, session_token("viewer"))
        assert await _may_view_screen(db_session, screen, session_token("owner"))
        assert not await _may_view_screen(db_session, screen, session_token("outsider"))
        assert not await _may_view_screen(db_session, screen, "bad-token")
        assert not await _may_view_screen(db_session, screen, None)

    _portal.call(ask)


def test_the_column_the_owner_kept_reading_is_gone(db_session, _portal):
    """`topics.session_placement` 这一列不在了。

    #1347 删掉了 `backend/app` 里最后一个读点——这个持有者在发布窗口里留的那个
    回落——列却留着：app 的发布不换 device connection owner 的镜像，那个镜像还
    `SELECT` 它，列一掉，它服务的每一条房间命令都报 `column
    topics.session_placement does not exist`，也就是本文件开头那次事故的形状。
    `Release device connection owner` 跑过之后，没有哪个还在跑的进程读它了。
    """

    async def ask():
        return await db_session.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = 'topics'"
                "   AND column_name = 'session_placement'"
            )
        )

    assert _portal.call(ask) == 0
