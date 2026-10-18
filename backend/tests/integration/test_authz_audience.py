"""A room audience's outsiders cost a fixed read, not one per seat.

An agent may read a room only when everyone the room reaches may read the
project too (``app.auth.audience.rooms_its_audience_may_read`` →
``app.auth.project_access.outsiders_reading``). The seats it has to ask about
are the room's roster minus the project's own people, and the roster's length
is the room's to choose — so the answer must not be paid for seat by seat. It
used to be: one ``may_read_project`` per unexplained seat, and each of those
reads the project, its owner, the exclusions and the team relations. A 20-seat
room the project does not list cost ~160 statements, and that was 169 of the
178 statements a steady work-lease spent.

These tests pin both halves of the change: the reads no longer grow with the
roster, and the answer per handle is the one ``may_read_project`` gives one at
a time — owner, team member, team admin, exclusion, roster row, recorded role,
stranger, AI teammate.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.auth.project_access import may_read_project, may_read_project_many
from app.domain.identity.models import AgentBinding
from app.domain.project.models import (
    ProjectMember,
    ProjectMemberExclusion,
    ProjectRole,
    ProjectRoleGrant,
)
from app.domain.topic.models import TopicMembership, TopicRole
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _people(client, *handles: str) -> None:
    for handle in handles:
        seed_user(client, handle)


def _on_the_app_loop(client, work):
    """Run ``work(session)`` on the loop the app's requests run on, and commit."""

    async def _run():
        async with client.test_request_factory() as session:  # type: ignore[attr-defined]
            result = await work(session)
            await session.commit()
            return result

    return client.portal.call(_run)  # type: ignore[attr-defined]


def _room(client, project_id: str, by: str, seats: list[str]) -> str:
    """A private channel in the project with exactly ``seats`` on its roster."""
    made = client.post(
        "/topics",
        json={"project_id": project_id, "title": "机密频道", "members_only": True},
        headers=session_auth_headers(by),
    )
    assert made.status_code == 200, made.text
    topic_id = uuid.UUID(made.json()["data"]["id"])

    async def _seat(session):
        start = datetime.now(UTC)
        session.add_all(
            [
                TopicMembership(
                    topic_id=topic_id,
                    member_handle=handle,
                    role=TopicRole.member,
                    created_at=start + timedelta(seconds=i),
                    updated_at=start + timedelta(seconds=i),
                )
                for i, handle in enumerate(seats)
            ]
        )

    _on_the_app_loop(client, _seat)
    return str(topic_id)


def _statements(client):
    """Record every statement the app engine runs until ``stop()``."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        seen.append(statement)

    engine = client.test_app_engine.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(engine, "before_cursor_execute", record)


def _outsiders(client, project_id: str, room_id: str) -> list[str]:
    from app.auth.project_access import outsiders_reading

    async def _ask(session):
        return await outsiders_reading(
            session,
            project_id=uuid.UUID(project_id),
            room_id=uuid.UUID(room_id),
        )

    return _on_the_app_loop(client, _ask)


def _many(client, project_id: str, handles: list[str]) -> dict[str, bool]:
    async def _ask(session):
        return await may_read_project_many(
            session, project_id=uuid.UUID(project_id), handles=handles
        )

    return _on_the_app_loop(client, _ask)


def _one_by_one(client, project_id: str, handles: list[str]) -> dict[str, bool]:
    async def _ask(session):
        return {
            handle: await may_read_project(
                session, project_id=uuid.UUID(project_id), handle=handle
            )
            for handle in handles
        }

    return _on_the_app_loop(client, _ask)


def _project(client) -> dict:
    """dave's project; ``teammate`` and ``teamadmin`` join its team."""
    _people(client, "dave", "teammate", "teamadmin")
    project = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    join_project_team(client, project["id"], "teammate")
    join_project_team(client, project["id"], "teamadmin", admin=True)
    return project


def test_the_audience_check_costs_the_same_for_three_seats_and_twenty(client):
    """A longer roster is not a longer conversation with the database."""
    project = _project(client)
    _people(client, *[f"seat-{i:02d}" for i in range(20)])
    short = _room(client, project["id"], "dave", [f"seat-{i:02d}" for i in range(3)])
    long = _room(client, project["id"], "dave", [f"seat-{i:02d}" for i in range(20)])

    small_seen, stop = _statements(client)
    try:
        small = _outsiders(client, project["id"], short)
    finally:
        stop()

    large_seen, stop = _statements(client)
    try:
        large = _outsiders(client, project["id"], long)
    finally:
        stop()

    assert len(small) == 3, small
    assert len(large) == 20, large
    assert len(large_seen) == len(small_seen), (
        f"{len(small_seen)} statements for 3 seats, {len(large_seen)} for 20"
    )
    # Per seat this room cost one project read plus ~8 reads per handle (~160).
    # The batched answer is a handful of reads whatever the roster holds.
    assert len(large_seen) <= 20, large_seen


def test_every_handle_gets_the_answer_asking_one_by_one_would_give(client):
    """The batch decides each handle the way ``standing`` does, every branch."""
    project = _project(client)
    pid = project["id"]
    _people(
        client,
        "rostered",
        "mentor",
        "excluded",
        "roster-out",
        "stranger",
        "agent-seat",
    )

    async def _relationships(session):
        project_id = uuid.UUID(pid)
        session.add_all(
            [
                ProjectMember(project_id=project_id, user_handle="rostered"),
                ProjectMember(project_id=project_id, user_handle="roster-out"),
                ProjectMember(project_id=project_id, user_handle="mentor"),
                ProjectRole(project_id=project_id, user_handle="mentor", role="mentor"),
                ProjectRoleGrant(
                    project_id=project_id,
                    role="mentor",
                    permissions=["export_project"],
                ),
                ProjectMemberExclusion(project_id=project_id, user_handle="excluded"),
                ProjectMemberExclusion(project_id=project_id, user_handle="roster-out"),
            ]
        )
        await session.flush()
        agent = await UserRepository(session).get_by_username("agent-seat")
        assert agent is not None
        session.add(AgentBinding(user_id=agent.id))
        await session.flush()

    _on_the_app_loop(client, _relationships)
    join_project_team(client, pid, "excluded")

    handles = [
        "dave",  # the project's owner
        "teammate",  # in the project through the team
        "teamadmin",  # a team admin, not excluded
        "excluded",  # a team member the project excluded
        "rostered",  # a roster row, not on the team
        "mentor",  # a roster row with a recorded role and a grant
        "roster-out",  # a roster row the project also excluded
        "stranger",  # a registered person in neither
        "agent-seat",  # an AI teammate, not on the roster
        "nobody-0000",  # no account at all
    ]
    batch = _many(client, pid, handles)
    each = _one_by_one(client, pid, handles)

    assert batch == each, {
        handle: (batch.get(handle), each.get(handle))
        for handle in handles
        if batch.get(handle) != each.get(handle)
    }
    assert batch == {
        "dave": True,
        "teammate": True,
        "teamadmin": True,
        "excluded": False,
        "rostered": True,
        "mentor": True,
        "roster-out": True,
        "stranger": False,
        "agent-seat": False,
        "nobody-0000": False,
    }
