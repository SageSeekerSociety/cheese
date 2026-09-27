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


def _project(client, owner: str | None = None) -> dict:
    body: dict = {"name": "P"}
    if owner is not None:
        body["owner_handle"] = owner
    r = post_project(client, json=body, headers=session_auth_headers("alice"))
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
        {"name": name, "owner_handle": owner, "team_id": team_id},
        headers=session_auth_headers(owner),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


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
    authority to stand on. Every project now belongs to a team — a project with
    no team given goes to its owner's personal team — so with no real owner there
    is nowhere for it to belong, and it is refused rather than made ownerless.
    Posted raw: the post_project helper would register an owner first."""
    r = client.post("/projects", json={"name": "无主项目"})

    assert r.status_code == 422, r.text
    assert "项目需要归属一个团队" in r.text


def test_creating_a_project_with_a_real_owner_stays_quiet(client, caplog):
    """The warning has to mean something — an ordinary create must not trip it."""
    with caplog.at_level("WARNING", logger="cheesex.projects"):
        _project(client, owner="alice")

    assert not [rec for rec in caplog.records if "without a real owner" in rec.message]
