"""The team is the way into a project; outsiders come as invited external members.

A project's people are its team's members, read from the team, plus external
members who accepted an invitation. The team's owner and admins, and the
project's owner, manage it. Rooms choose among the project's people.
"""

import asyncio
from datetime import UTC, datetime

from tests.conftest import seed_user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def team_of(client, *, owner: str, admins=(), members=()) -> int:
    """A shared team with real users in the given roles; returns its id."""
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    for handle in (owner, *admins, *members):
        seed_user(client, handle)
    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team = Team(
                name=f"team of {owner}",
                handle=f"t-{owner}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            users = UserRepository(session)
            for handles, role in (
                ((owner,), TeamMemberRole.OWNER),
                (admins, TeamMemberRole.ADMIN),
                (members, TeamMemberRole.MEMBER),
            ):
                for handle in handles:
                    user = await users.get_by_username(handle)
                    assert user is not None
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


def leave_team(client, team_id: int, handle: str) -> None:
    from sqlalchemy import update

    from app.domain.team.models import TeamUserRelation
    from app.domain.user.repositories import UserRepository

    async def _leave() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            await session.execute(
                update(TeamUserRelation)
                .where(
                    TeamUserRelation.team_id == team_id,
                    TeamUserRelation.user_id == user.id,
                )
                .values(deleted_at=datetime.now(UTC))
            )
            await session.commit()

    asyncio.run(_leave())


def project_in(client, team_id: int, *, owner: str, name: str = "P") -> str:
    r = client.post(
        "/projects",
        json={"name": name, "owner_handle": owner, "team_id": team_id},
        headers=auth(seed_user(client, owner)),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def can_enter(client, pid: str, handle: str) -> bool:
    r = client.get(f"/projects/{pid}/members", headers=auth(seed_user(client, handle)))
    return r.status_code == 200


def roster(client, pid: str, viewer: str) -> dict[str, dict]:
    rows = client.get(
        f"/projects/{pid}/members", headers=auth(seed_user(client, viewer))
    ).json()["data"]["data"]
    return {r["user_handle"]: r for r in rows if not r["agent"]}


def invite(client, pid: str, *, by: str, who: str):
    return client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": who},
        headers=auth(seed_user(client, by)),
    )


def accept(client, invitation_id: str, who: str) -> None:
    r = client.post(
        f"/invitations/{invitation_id}/respond",
        json={"accept": True},
        headers=auth(seed_user(client, who)),
    )
    assert r.status_code == 200, r.text


def test_a_project_needs_a_team(client):
    r = client.post(
        "/projects", json={"name": "loose", "owner_handle": "nobody-registered"}
    )
    assert r.status_code == 422
    assert "团队" in r.text


def test_a_personal_project_belongs_to_the_owners_personal_team(client):
    token = seed_user(client, "solo1")
    r = client.post(
        "/projects", json={"name": "mine", "owner_handle": "solo1"}, headers=auth(token)
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["team_handle"] == "solo1"


def test_leaving_the_team_is_leaving_its_projects(client):
    tid = team_of(client, owner="own1", members=("mem1",))
    pid = project_in(client, tid, owner="own1")
    assert roster(client, pid, "mem1")["mem1"]["source"] == "team"
    leave_team(client, tid, "mem1")
    assert not can_enter(client, pid, "mem1")


def test_someone_who_joins_the_team_later_is_in_its_projects(client):
    tid = team_of(client, owner="own2")
    pid = project_in(client, tid, owner="own2")
    seed_user(client, "late2")
    assert not can_enter(client, pid, "late2")
    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    async def _join() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username("late2")
            session.add(
                TeamUserRelation(
                    team_id=tid,
                    user_id=user.id,
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_join())
    assert can_enter(client, pid, "late2")


def test_an_external_member_joins_by_accepting_an_invitation(client):
    tid = team_of(client, owner="own3")
    pid = project_in(client, tid, owner="own3")
    other = project_in(client, tid, owner="own3", name="other")
    seed_user(client, "guest3")
    r = invite(client, pid, by="own3", who="guest3")
    assert r.status_code == 200, r.text
    assert not can_enter(client, pid, "guest3")
    accept(client, r.json()["data"]["id"], "guest3")
    assert can_enter(client, pid, "guest3")
    assert roster(client, pid, "own3")["guest3"]["source"] == "external"
    # They are in this one project, not in the team's others.
    assert not can_enter(client, other, "guest3")


def test_team_members_are_not_invited(client):
    tid = team_of(client, owner="own4", members=("mem4",))
    pid = project_in(client, tid, owner="own4")
    r = invite(client, pid, by="own4", who="mem4")
    assert r.status_code == 422
    assert "团队成员不需要邀请" in r.text


def test_a_team_admin_manages_the_project_and_a_plain_member_does_not(client):
    tid = team_of(client, owner="own5", admins=("adm5",), members=("mem5",))
    pid = project_in(client, tid, owner="own5")
    seed_user(client, "g5a")
    seed_user(client, "g5b")
    assert invite(client, pid, by="adm5", who="g5a").status_code == 200
    assert invite(client, pid, by="mem5", who="g5b").status_code == 403
    flags = {
        who: client.get(
            f"/projects/{pid}", headers=auth(seed_user(client, who))
        ).json()["data"]["can_manage_members"]
        for who in ("own5", "adm5", "mem5")
    }
    assert flags == {"own5": True, "adm5": True, "mem5": False}


def test_removing_an_external_member_ends_their_access(client):
    tid = team_of(client, owner="own6", admins=("adm6",))
    pid = project_in(client, tid, owner="own6")
    seed_user(client, "guest6")
    accept(
        client,
        invite(client, pid, by="own6", who="guest6").json()["data"]["id"],
        "guest6",
    )
    r = client.delete(
        f"/projects/{pid}/members/guest6", headers=auth(seed_user(client, "adm6"))
    )
    assert r.status_code == 200, r.text
    assert not can_enter(client, pid, "guest6")


def test_an_external_member_may_leave_and_a_team_member_is_sent_to_the_team(client):
    tid = team_of(client, owner="own7", members=("mem7",))
    pid = project_in(client, tid, owner="own7")
    seed_user(client, "guest7")
    accept(
        client,
        invite(client, pid, by="own7", who="guest7").json()["data"]["id"],
        "guest7",
    )
    left = client.delete(
        f"/projects/{pid}/membership", headers=auth(seed_user(client, "guest7"))
    )
    assert left.status_code == 200, left.text
    assert not can_enter(client, pid, "guest7")
    stay = client.delete(
        f"/projects/{pid}/membership", headers=auth(seed_user(client, "mem7"))
    )
    assert stay.status_code == 409
    assert "退出团队" in stay.text


def test_people_are_not_added_to_a_project_directly(client):
    tid = team_of(client, owner="own8")
    pid = project_in(client, tid, owner="own8")
    seed_user(client, "guest8")
    r = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "guest8"},
        headers=auth(seed_user(client, "own8")),
    )
    assert r.status_code == 422
    assert not can_enter(client, pid, "guest8")


def test_a_room_only_takes_the_projects_people(client):
    tid = team_of(client, owner="own9", members=("mem9",))
    pid = project_in(client, tid, owner="own9")
    token = seed_user(client, "own9")
    room = client.post(
        "/topics",
        json={"project_id": pid, "title": "Room", "created_by": "own9"},
        headers=auth(token),
    ).json()["data"]
    seed_user(client, "stranger9")
    refused = client.post(
        f"/topics/{room['id']}/members",
        json={"handle": "stranger9", "role": "member"},
        headers=auth(token),
    )
    assert refused.status_code == 422
    assert "只能添加项目成员" in refused.text
    added = client.post(
        f"/topics/{room['id']}/members",
        json={"handle": "mem9", "role": "member"},
        headers=auth(token),
    )
    assert added.status_code == 200, added.text


def test_the_project_goes_only_to_someone_on_its_team(client):
    tid = team_of(client, owner="own10", members=("mem10",))
    pid = project_in(client, tid, owner="own10")
    seed_user(client, "guest10")
    accept(
        client,
        invite(client, pid, by="own10", who="guest10").json()["data"]["id"],
        "guest10",
    )
    token = seed_user(client, "own10")
    refused = client.put(
        f"/projects/{pid}/owner", json={"owner_handle": "guest10"}, headers=auth(token)
    )
    assert refused.status_code == 422
    moved = client.put(
        f"/projects/{pid}/owner", json={"owner_handle": "mem10"}, headers=auth(token)
    )
    assert moved.status_code == 200, moved.text


def test_an_account_is_found_only_by_its_exact_username_or_email(client):
    token = seed_user(client, "finder11")
    seed_user(client, "Findable11")
    by_name = client.get(
        "/users/lookup", params={"q": "findable11"}, headers=auth(token)
    )
    assert by_name.status_code == 200, by_name.text
    assert by_name.json()["data"]["handle"] == "Findable11"
    by_email = client.get(
        "/users/lookup", params={"q": "FINDABLE11@example.com"}, headers=auth(token)
    )
    assert by_email.json()["data"]["handle"] == "Findable11"
    partial = client.get("/users/lookup", params={"q": "findable"}, headers=auth(token))
    assert partial.status_code == 404
    assert client.get("/users/lookup", params={"q": "findable11"}).status_code == 401


def test_a_pending_invitation_names_the_person(client):
    tid = team_of(client, owner="own12")
    pid = project_in(client, tid, owner="own12")
    seed_user(client, "guest12")
    invite(client, pid, by="own12", who="guest12")
    [row] = client.get(
        f"/projects/{pid}/invitations", headers=auth(seed_user(client, "own12"))
    ).json()["data"]["data"]
    assert (row["invitee_handle"], row["invitee_name"]) == ("guest12", "guest12")
