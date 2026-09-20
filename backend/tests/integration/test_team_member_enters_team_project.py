"""团队成员进得了团队项目：门禁和列表对「项目成员」的定义要一致。

`ProjectRepository.list_visible_to` lists a project under three claims — you
own it, you are on its roster, or it belongs to a team you are in. The guards
(`authorize_project`, and the project-member check inside `authorize_topic`)
accepted only the first two. So a teammate saw the project on the team page and
in the sidebar rail, clicked in, and the topic list answered 403:

    --- bob, accepted alice's team invitation a minute ago ---
      GET /projects?team_id=1499            -> 200  (the card he clicked)
      GET /projects/{id}                    -> 200
      GET /topics?project_id={id}           -> 403  你不是这个项目的成员，无权查看

Measured on dev 2026-09-04 (team 1499, project 4abe8cf5…), not inferred. A
1–3 person team cannot collaborate in one project under that rule, which is
the whole point of 项目归团队.

Everything below asserts on status codes a browser would receive. The
boundaries that must NOT move are pinned too: a stranger, a member of some
other team, and a member of the owner's *personal* team (there are none but
the owner) all stay outside.
"""

import asyncio
from datetime import UTC, datetime

from tests.conftest import seed_user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _team(client, *, owner: str, members: tuple[str, ...]) -> int:
    """A real team row with ``owner`` as OWNER and ``members`` as MEMBER.

    Users are created through ``seed_user`` so they hold int ids — team
    membership is keyed by user id, unlike every other roster in the platform.
    """
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    for handle in (owner, *members):
        seed_user(client, handle)

    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team = Team(
                name=f"team-of-{owner}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            users = UserRepository(session)
            for handle, role in (
                (owner, TeamMemberRole.OWNER),
                *((m, TeamMemberRole.MEMBER) for m in members),
            ):
                user = await users.get_by_username(handle)
                assert user is not None, handle
                session.add(
                    TeamUserRelation(
                        team_id=team.id,
                        user_id=user.id,
                        role=role,
                        created_at=now,
                        updated_at=now,
                    )
                )
            holder["id"] = team.id
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def _team_project(client, *, owner: str, team_id: int | None) -> tuple[str, str]:
    """``(project_id, root_topic_id)`` of a project ``owner`` creates."""
    body: dict[str, object] = {"name": "P", "owner_handle": owner}
    if team_id is not None:
        body["team_id"] = team_id
    r = client.post("/projects", json=body, headers=_bearer(seed_user(client, owner)))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return data["id"], data["root_topic_id"]


def _can_enter(client, handle: str, pid: str, root_tid: str) -> tuple[int, int]:
    """Status codes of the two calls the workspace makes when it opens: the
    topic list (project guard) and the 总览 header (topic guard)."""
    h = _bearer(seed_user(client, handle))
    topics = client.get("/topics", params={"project_id": pid}, headers=h)
    header = client.get(f"/topics/{root_tid}", headers=h)
    return topics.status_code, header.status_code


def test_a_teammate_enters_the_team_project(client):
    """The one that was broken: on the team, not on the project roster."""
    team_id = _team(client, owner="alice", members=("bob",))
    pid, root = _team_project(client, owner="alice", team_id=team_id)
    assert _can_enter(client, "bob", pid, root) == (200, 200)


def test_the_owner_still_enters(client):
    """The half that must not change."""
    team_id = _team(client, owner="alice", members=("bob",))
    pid, root = _team_project(client, owner="alice", team_id=team_id)
    assert _can_enter(client, "alice", pid, root) == (200, 200)


def test_a_stranger_stays_outside(client):
    team_id = _team(client, owner="alice", members=("bob",))
    pid, root = _team_project(client, owner="alice", team_id=team_id)
    assert _can_enter(client, "mallory", pid, root) == (403, 403)


def test_a_member_of_another_team_stays_outside(client):
    """Being on *a* team is not being on *this* team."""
    _team(client, owner="carol", members=("dave",))
    team_id = _team(client, owner="alice", members=("bob",))
    pid, root = _team_project(client, owner="alice", team_id=team_id)
    assert _can_enter(client, "dave", pid, root) == (403, 403)


def test_a_project_without_a_team_admits_nobody_else(client):
    """A project created from the rail ＋ lands in the owner's personal team,
    whose only member is the owner — so it stays exactly as private as before."""
    seed_user(client, "bob")
    pid, root = _team_project(client, owner="alice", team_id=None)
    assert _can_enter(client, "alice", pid, root) == (200, 200)
    assert _can_enter(client, "bob", pid, root) == (403, 403)
