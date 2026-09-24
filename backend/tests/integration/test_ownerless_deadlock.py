"""A room that lost its owner is never a dead end, and never open to everyone.

`TopicMemberService._require_manager` lets whoever manages the project — its
owner, or an owner/admin of its team — step into a room that has nobody who can
manage it. Every project belongs to a team and every team has an owner, so there
is always someone; a plain member of the team never gets that power, and a room
that still has an owner is never overridden.
"""

import uuid

from tests.integration.conftest import (
    join_project_team,
    new_project,
    session_auth_headers,
)


def _strip_topic_managers(client, topic_id: str) -> None:
    """Leave the room with nobody who can manage it (the state under test)."""
    import asyncio

    from sqlalchemy import delete

    from app.domain.topic.models import TopicMembership, TopicRole

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                delete(TopicMembership).where(
                    TopicMembership.topic_id == uuid.UUID(topic_id),
                    TopicMembership.role.in_([TopicRole.owner, TopicRole.admin]),
                )
            )
            await session.commit()

    asyncio.run(_go())


def _claim_owner(client, topic_id: str, who: str):
    return client.post(
        f"/topics/{topic_id}/members",
        json={"handle": who, "role": "owner", "actor": who},
        headers=session_auth_headers(who),
    )


def test_the_project_owner_steps_into_a_room_nobody_manages(client):
    p = new_project(client, name="有主", owner="dave")
    _strip_topic_managers(client, p["root_topic_id"])

    assert _claim_owner(client, p["root_topic_id"], "dave").status_code == 200


def test_a_team_admin_steps_in_and_a_plain_member_does_not(client):
    p = new_project(client, name="团队的项目", owner="dave")
    join_project_team(client, p["id"], "carol", admin=True)
    join_project_team(client, p["id"], "alice")
    _strip_topic_managers(client, p["root_topic_id"])

    assert _claim_owner(client, p["root_topic_id"], "alice").status_code == 403
    assert _claim_owner(client, p["root_topic_id"], "carol").status_code == 200


def test_an_outsider_never_steps_in(client):
    p = new_project(client, name="有主", owner="dave")
    _strip_topic_managers(client, p["root_topic_id"])

    assert _claim_owner(client, p["root_topic_id"], "mallory").status_code == 403


def test_a_healthy_room_keeps_its_owner_safe(client):
    """The hatch opens only while the ROOM has no manager. A room with an owner
    is never overridden, not even by someone who manages the project."""
    p = new_project(client, name="有主", owner="dave")
    join_project_team(client, p["id"], "carol", admin=True)
    _strip_topic_managers(client, p["root_topic_id"])
    assert _claim_owner(client, p["root_topic_id"], "carol").status_code == 200

    # carol now owns the room; the project's owner does not take it over.
    assert _claim_owner(client, p["root_topic_id"], "dave").status_code == 403
