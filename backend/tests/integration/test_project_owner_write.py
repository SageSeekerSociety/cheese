"""PUT /projects/{id}/owner — the missing writer for `project.owner_handle` (#315).

The field had seven readers and one writer (`POST /projects`), so a project
that came into existence without an owner could never acquire one. That is not
hypothetical: on the dogfood project the column sat NULL for six days. Nothing
broke loudly — every reader falls back to someone else — so the project's
owner-level authority silently collapsed, and nobody noticed until someone went
looking.

These tests pin the way out and the rails on it: only someone who manages the
project (its owner, or a team owner/admin) may move ownership; a project that
belongs to a SHARED team may only go to a member of that team; and a project
that is the transferor's own — it sits in their personal team — moves into the
recipient's personal team instead, so the transferor really is out (2026-09-27,
caisongyang's decision: 选中谁就立刻换 owner，项目跟着人走).

That last case is why the recipient no longer has to be on the project's team:
a personal project has no other stakeholder to stay with, so the project moves.
Handing a SHARED team's project to an outsider is still refused — the project
would stay in that team, where its former owner keeps reading it and could take
the owner back.
"""

import asyncio
import uuid

from app.domain.team.repositories import TeamRepository
from tests.integration.conftest import (
    a_team,
    add_external_member,
    join_project_team,
    post_project,
    registered,
    session_auth_headers,
)


def _project(client, owner: str = "alice") -> dict:
    r = post_project(client, json={"name": "P"}, headers=session_auth_headers(owner))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _register(client, handle: str) -> int:
    """Make ``handle`` a real account (and no personal team yet — the transfer
    is what provisions it)."""

    async def _seed() -> int:
        async with client.test_factory() as session:
            user_id = await registered(session, handle)
            await session.commit()
            return user_id

    return asyncio.run(_seed())


def _personal_team_id(client, handle: str) -> int | None:
    """The id of ``handle``'s personal team, or None when they have none yet."""
    from app.domain.user.repositories import UserRepository

    async def _read() -> int | None:
        async with client.test_factory() as session:
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            team = await TeamRepository(session).get_personal_team(user.id)
            return team.id if team is not None else None

    return asyncio.run(_read())


def _shared_team_project(client, *, owner: str = "alice", name: str = "P") -> dict:
    """A project that belongs to a SHARED team — one with other people in it,
    which is the case where the project cannot follow its owner."""

    async def _make() -> int:
        async with client.test_factory() as session:
            team_id = await a_team(session, owner_handle=owner)
            await session.commit()
            return team_id

    team_id = asyncio.run(_make())
    resp = post_project(
        client,
        {"name": name, "team_id": team_id},
        headers=session_auth_headers(owner),
        owner=owner,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _root_topic(client, project_id: str, *, actor: str = "alice") -> dict:
    """The project's 综合 channel — everyone in the project is in it."""
    r = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers(actor)
    )
    assert r.status_code == 200, r.text
    root = next(
        (row for row in r.json()["data"]["data"] if row.get("kind") == "root"), None
    )
    assert root is not None, r.text
    return root


def _room_roster(client, topic_id: str, *, actor: str) -> list[dict]:
    r = client.get(f"/topics/{topic_id}/members", headers=session_auth_headers(actor))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _seat(client, topic_id: str, handle: str, *, actor: str) -> None:
    """Put ``handle`` in the channel, as someone who manages it."""
    r = client.post(
        f"/topics/{topic_id}/members",
        json={"handle": handle},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200, r.text


def _add_member(client, project_id: str, handle: str, *, admin: bool = False) -> None:
    """``handle`` joins the project's team; ``admin`` makes them a team admin."""
    join_project_team(client, project_id, handle, admin=admin)


def _set_owner(client, project_id: str, handle: str, *, actor: str):
    return client.put(
        f"/projects/{project_id}/owner",
        json={"owner_handle": handle},
        headers=session_auth_headers(actor),
    )


def _owner_of(client, project_id: str) -> str | None:
    return client.get(f"/projects/{project_id}").json()["data"]["owner_handle"]


def test_the_owner_can_hand_the_project_to_another_member(client):
    p = _project(client, owner="alice")
    _add_member(client, p["id"], "bob")

    r = _set_owner(client, p["id"], "bob", actor="alice")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["owner_handle"] == "bob"
    assert _owner_of(client, p["id"]) == "bob"
    # A teammate receiving it changes the owner and NOTHING else: the project
    # is still the team's, byte for byte.
    assert r.json()["data"]["team_id"] == p["team_id"]


def test_a_personal_project_follows_the_person_it_is_given_to(client):
    """The project is the transferor's own — it sits in their personal team — so
    handing it to someone outside that team moves it into the recipient's
    personal team. 项目跟着人走."""
    p = _project(client, owner="alice")
    _register(client, "carol")
    assert _personal_team_id(client, "carol") is None  # provisioned by the move

    r = _set_owner(client, p["id"], "carol", actor="alice")

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["owner_handle"] == "carol"
    carol_team = _personal_team_id(client, "carol")
    assert carol_team is not None
    assert data["team_id"] == carol_team
    assert data["team_id"] != p["team_id"]

    # And the move is a move, not a swapped field: `Project.team_id`'s readers
    # (the team's 项目 page goes through `ProjectService.list_for_team`) now see
    # the project on the recipient's side and no longer on the giver's.
    mine = client.get(
        f"/projects?team_id={carol_team}", headers=session_auth_headers("carol")
    )
    assert mine.status_code == 200, mine.text
    assert [row["id"] for row in mine.json()["data"]["data"]] == [p["id"]]
    alices_team = _personal_team_id(client, "alice")
    assert alices_team is not None
    gone = client.get(
        f"/projects?team_id={alices_team}", headers=session_auth_headers("alice")
    )
    assert gone.status_code == 200, gone.text
    assert [row["id"] for row in gone.json()["data"]["data"]] == []


def test_the_transferor_is_out_for_good(client):
    """A transfer that leaves the giver reading the project is a loan, not a
    transfer: it would still be in their team and they still its team's owner."""
    p = _project(client, owner="alice")
    _register(client, "carol")
    assert _set_owner(client, p["id"], "carol", actor="alice").status_code == 200

    read = client.get(f"/projects/{p['id']}", headers=session_auth_headers("alice"))
    assert read.status_code in (403, 404), read.text
    # And the same route that gave it away cannot take it back.
    take_back = _set_owner(client, p["id"], "alice", actor="alice")
    assert take_back.status_code == 404, take_back.text
    assert _owner_of(client, p["id"]) == "carol"
    # Nor through any other door: the project's records are closed to them too.
    listed = client.get(
        f"/topics?project_id={p['id']}", headers=session_auth_headers("alice")
    )
    assert listed.status_code in (403, 404), listed.text


def _channel(client, project_id: str, *, by: str = "alice") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "她建的频道"},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_the_transferor_loses_the_projects_rooms_too(client):
    """项目那一层的门关上不算数。

    A seat in a channel admits its holder to that channel by itself
    (`authorize_topic_access` reads it before it asks about the project), so a
    transfer that only swaps `owner_handle` would leave the giver in every
    channel they had joined: still speaking there, still managing the ones they
    created. That is the difference between 转让 and 借, so the transfer takes
    the seats away.

    Both directions are asserted — the same calls must have worked BEFORE the
    transfer, or the 403 afterwards proves only that the room was shut to
    everyone.
    """
    p = _project(client, owner="alice")
    _register(client, "carol")
    root = _root_topic(client, p["id"])
    mine = _channel(client, p["id"])
    for room in (root["id"], mine):
        before = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
        assert before.status_code == 200, before.text

    assert _set_owner(client, p["id"], "carol", actor="alice").status_code == 200

    for room in (root["id"], mine):
        after = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
        assert after.status_code in (403, 404), after.text
        people = client.get(
            f"/topics/{room}/members", headers=session_auth_headers("alice")
        )
        assert people.status_code in (403, 404), people.text
    assert all(
        row["member_handle"] != "alice"
        for row in _room_roster(client, mine, actor="carol")
    )


def test_the_recipient_manages_the_projects_channels(client):
    """接手人管得了这个项目的每一个频道，包括原所有者建的那些——管项目的人就管
    它的频道，不用一把一把地接椅子。"""
    p = _project(client, owner="alice")
    _register(client, "carol")
    mine = _channel(client, p["id"])
    # Before the transfer she cannot even reach the channel, let alone manage it.
    early = client.post(
        f"/topics/{mine}/members",
        json={"handle": "carol"},
        headers=session_auth_headers("carol"),
    )
    assert early.status_code == 403, early.text

    assert _set_owner(client, p["id"], "carol", actor="alice").status_code == 200

    add_external_member(client, p["id"], "dana", by="carol")
    seated = client.post(
        f"/topics/{mine}/members",
        json={"handle": "dana"},
        headers=session_auth_headers("carol"),
    )
    assert seated.status_code == 200, seated.text
    root = _root_topic(client, p["id"], actor="carol")
    assert "carol" in {
        row["member_handle"] for row in _room_roster(client, root["id"], actor="carol")
    }


def test_a_team_member_keeps_the_projects_rooms(client):
    """The other branch must not be swept up by this.

    When the recipient is already on the project's team the giver stays in the
    project — by way of that team — so their channel seats are not leftovers to
    be cleaned up. Revoking them there would evict someone who is still a
    member, which is why the seats go on the personal-project branch only.
    """
    p = _shared_team_project(client)
    _add_member(client, p["id"], "bob")
    mine = _channel(client, p["id"])

    r = _set_owner(client, p["id"], "bob", actor="alice")

    assert r.status_code == 200, r.text
    still = client.get(f"/topics/{mine}", headers=session_auth_headers("alice"))
    assert still.status_code == 200, still.text
    rows = _room_roster(client, mine, actor="alice")
    alice = next(row for row in rows if row["member_handle"] == "alice")
    assert alice["role"] == "owner"


def test_the_recipients_own_seats_are_left_alone(client):
    """The natural way a personal project changes hands: invite someone in, put
    them in a channel, then hand the project to them. Their seat stays as it
    was; only the giver's go."""
    p = _project(client, owner="alice")
    mine = _channel(client, p["id"])
    add_external_member(client, p["id"], "dana", by="alice")
    _seat(client, mine, "dana", actor="alice")

    r = _set_owner(client, p["id"], "dana", actor="alice")

    assert r.status_code == 200, r.text
    rows = _room_roster(client, mine, actor="dana")
    assert any(row["member_handle"] == "dana" for row in rows)
    assert all(row["member_handle"] != "alice" for row in rows)
    gone = client.get(f"/topics/{mine}", headers=session_auth_headers("alice"))
    assert gone.status_code in (403, 404), gone.text


def test_an_unknown_recipient_is_refused_by_name(client):
    """A handle nobody answers to is a 422 that says so — not a 500, and not a
    silent "not a member" about a person who does not exist."""
    p = _project(client, owner="alice")

    r = _set_owner(client, p["id"], "nobody-at-all", actor="alice")

    assert r.status_code == 422, r.text
    assert "账号" in r.json()["message"]
    assert _owner_of(client, p["id"]) == "alice"


def test_a_team_admin_can_take_the_project(client):
    """The escape from #315's actual state: an admin of the project's team
    answers for it and can make themselves its owner. Before this route there
    was no way out at all — the field was write-once at create."""
    p = _project(client, owner="alice")
    _add_member(client, p["id"], "dana", admin=True)

    assert _set_owner(client, p["id"], "dana", actor="dana").status_code == 200
    assert _owner_of(client, p["id"]) == "dana"


def test_a_plain_member_cannot_take_the_project(client):
    p = _project(client, owner="alice")
    _add_member(client, p["id"], "erin")

    r = _set_owner(client, p["id"], "erin", actor="erin")

    # 404, not 403: an outsider must not learn the project exists.
    assert r.status_code == 404
    assert _owner_of(client, p["id"]) == "alice"


def test_an_outsider_cannot_take_the_project(client):
    p = _project(client, owner="alice")

    assert _set_owner(client, p["id"], "mallory", actor="mallory").status_code == 404
    assert _owner_of(client, p["id"]) == "alice"


def test_an_anonymous_caller_cannot_take_the_project(client):
    p = _project(client, owner="alice")

    r = client.put(f"/projects/{p['id']}/owner", json={"owner_handle": "mallory"})

    assert r.status_code == 404
    assert _owner_of(client, p["id"]) == "alice"


def test_a_shared_team_project_cannot_be_handed_to_someone_off_the_team(client):
    """The project belongs to a team with other people in it, so it stays that
    team's: an outside owner would hold a project still sitting in the giver's
    team, where the giver keeps reading it and could take it back."""
    p = _shared_team_project(client)
    _register(client, "stranger")

    r = _set_owner(client, p["id"], "stranger", actor="alice")

    assert r.status_code == 422, r.text
    assert "团队" in r.json()["message"]
    assert _owner_of(client, p["id"]) == "alice"


def test_a_shared_team_project_cannot_go_to_an_external_member(client):
    """An external member sees this one project and nothing else of the team;
    the project is the team's, so its owner is someone from the team."""
    p = _shared_team_project(client)
    add_external_member(client, p["id"], "guest", by="alice")

    r = _set_owner(client, p["id"], "guest", actor="alice")

    assert r.status_code == 422, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_an_empty_handle_is_rejected_rather_than_blanking_the_owner(client):
    """`owner_handle = ""` is exactly the NULL-ish state this route exists to
    escape — the write path must not be a way back into it."""
    p = _project(client, owner="alice")

    r = client.put(
        f"/projects/{p['id']}/owner",
        json={"owner_handle": "   "},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 422
    assert _owner_of(client, p["id"]) == "alice"


def test_a_missing_project_is_a_404_not_a_500(client):
    r = client.put(
        f"/projects/{uuid.uuid4()}/owner",
        json={"owner_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 404


def test_creating_a_project_without_a_real_owner_is_refused(client):
    """#315: a project with no owner who is a person had nothing for its
    authority to stand on. The owner is whoever is signed in, so a request
    that names nobody has no owner to give the project and is refused rather
    than made ownerless."""
    r = client.post("/projects", json={"name": "无主项目"})

    assert r.status_code == 401, r.text
