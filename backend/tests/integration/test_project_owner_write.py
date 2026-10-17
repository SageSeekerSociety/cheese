"""Handing a project over (#315, #3830).

`project.owner_handle` once had seven readers and one writer, so a project that
came into existence without an owner could never acquire one. Its writer is now
an offer: the owner offers the project to someone already in it (not a mentor),
and nothing changes until that person accepts. Then they own it and the former
owner stays on as an admin — nobody is put out of the project by handing it
over. A personal project cannot stay in a space that is one other person's, so
it moves into the recipient's own; a team's project stays in the team.
"""

import asyncio
import uuid

import pytest

from app.domain.team.repositories import TeamRepository
from tests.integration.conftest import (
    a_team,
    add_external_member,
    hand_over,
    join_project_team,
    offer_project,
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


def _project_as(client, project_id: str, handle: str) -> dict:
    r = client.get(f"/projects/{project_id}", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _owner_of(client, project_id: str, *, reader: str = "alice") -> str | None:
    return _project_as(client, project_id, reader)["owner_handle"]


def _respond(client, transfer_id: str, *, who: str, accept: bool):
    return client.post(
        f"/transfers/{transfer_id}/respond",
        json={"accept": accept},
        headers=session_auth_headers(who),
    )


def _rename(client, topic_id: str, title: str, *, who: str):
    return client.post(
        f"/topics/{topic_id}/title",
        json={"title": title},
        headers=session_auth_headers(who),
    )


def test_the_owner_hands_a_team_project_to_another_member(client):
    p = _shared_team_project(client)
    _add_member(client, p["id"], "bob")

    r = hand_over(client, p["id"], "bob", by="alice")

    assert r.status_code == 200, r.text
    after = _project_as(client, p["id"], "bob")
    assert after["owner_handle"] == "bob"
    assert after["my_role"] == "owner"
    # A teammate receiving it changes the owner and NOTHING else: the project
    # is still the team's.
    assert after["team_id"] == p["team_id"]


def test_nothing_changes_until_the_recipient_accepts(client):
    p = _shared_team_project(client)
    _add_member(client, p["id"], "bob")

    offered = offer_project(client, p["id"], "bob", by="alice")

    assert offered.status_code == 200, offered.text
    waiting = _project_as(client, p["id"], "alice")
    assert waiting["owner_handle"] == "alice"
    assert waiting["pending_transfer"]["to_handle"] == "bob"

    declined = _respond(client, offered.json()["data"]["id"], who="bob", accept=False)

    assert declined.status_code == 200, declined.text
    after = _project_as(client, p["id"], "alice")
    assert after["owner_handle"] == "alice"
    assert after["pending_transfer"] is None


def test_only_the_recipient_answers_the_offer(client):
    p = _shared_team_project(client)
    _add_member(client, p["id"], "bob")
    _add_member(client, p["id"], "carol")
    offered = offer_project(client, p["id"], "bob", by="alice")
    assert offered.status_code == 200, offered.text

    taken = _respond(client, offered.json()["data"]["id"], who="carol", accept=True)

    assert taken.status_code == 403, taken.text
    assert _owner_of(client, p["id"]) == "alice"


def test_a_personal_project_follows_the_person_it_is_given_to(client):
    """The project is the transferor's own — it sits in their personal team — so
    handing it to someone outside that team moves it into the recipient's
    personal team. 项目跟着人走."""
    p = _project(client, owner="alice")
    add_external_member(client, p["id"], "carol", by="alice")

    r = hand_over(client, p["id"], "carol", by="alice")

    assert r.status_code == 200, r.text
    data = _project_as(client, p["id"], "carol")
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


def test_the_former_owner_stays_as_an_admin(client):
    """Handing the project over does not put the giver out: they stay in it as
    an admin. They no longer own it, so they cannot offer it back."""
    p = _project(client, owner="alice")
    add_external_member(client, p["id"], "carol", by="alice")
    assert hand_over(client, p["id"], "carol", by="alice").status_code == 200

    mine = _project_as(client, p["id"], "alice")
    assert mine["my_role"] == "admin"
    listed = client.get(
        f"/topics?project_id={p['id']}", headers=session_auth_headers("alice")
    )
    assert listed.status_code == 200, listed.text

    take_back = offer_project(client, p["id"], "alice", by="alice")
    assert take_back.status_code == 403, take_back.text
    assert _owner_of(client, p["id"], reader="carol") == "carol"


def _channel(client, project_id: str, *, by: str = "alice") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "她建的频道"},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_the_recipient_manages_the_projects_channels(client):
    """接手人管得了这个项目的每一个频道，包括原所有者建的那些——管项目的人就管
    它的频道，不用一把一把地接椅子。"""
    p = _project(client, owner="alice")
    add_external_member(client, p["id"], "carol", by="alice")
    mine = _channel(client, p["id"])
    # Before the transfer she is a plain member: someone else's channel is not
    # hers to rename.
    early = _rename(client, mine, "她改的名", who="carol")
    assert early.status_code == 403, early.text

    assert hand_over(client, p["id"], "carol", by="alice").status_code == 200

    renamed = _rename(client, mine, "她改的名", who="carol")
    assert renamed.status_code == 200, renamed.text
    root = _root_topic(client, p["id"], actor="carol")
    assert "carol" in {
        row["member_handle"] for row in _room_roster(client, root["id"], actor="carol")
    }


@pytest.mark.parametrize("shared", [True, False], ids=["team", "personal"])
def test_the_former_owner_keeps_the_projects_rooms(client, shared):
    """The giver stays in the project, so their channel seats are not leftovers
    to be cleaned up: they keep the channel they made, as its owner."""
    if shared:
        p = _shared_team_project(client)
        _add_member(client, p["id"], "bob")
    else:
        p = _project(client, owner="alice")
        add_external_member(client, p["id"], "bob", by="alice")
    mine = _channel(client, p["id"])

    r = hand_over(client, p["id"], "bob", by="alice")

    assert r.status_code == 200, r.text
    still = client.get(f"/topics/{mine}", headers=session_auth_headers("alice"))
    assert still.status_code == 200, still.text
    rows = _room_roster(client, mine, actor="alice")
    alice = next(row for row in rows if row["member_handle"] == "alice")
    assert alice["role"] == "owner"


def test_the_recipients_own_seats_are_left_alone(client):
    """The natural way a personal project changes hands: invite someone in, put
    them in a channel, then hand the project to them. Their seat stays as it
    was."""
    p = _project(client, owner="alice")
    mine = _channel(client, p["id"])
    add_external_member(client, p["id"], "dana", by="alice")
    _seat(client, mine, "dana", actor="alice")

    r = hand_over(client, p["id"], "dana", by="alice")

    assert r.status_code == 200, r.text
    rows = _room_roster(client, mine, actor="dana")
    assert any(row["member_handle"] == "dana" for row in rows)


@pytest.mark.parametrize("to", ["nobody-at-all", "stranger"])
def test_the_project_goes_only_to_someone_in_it(client, to):
    """A handle nobody answers to, or a person who is not in the project, is a
    422 — not a 500 — and the owner stays."""
    p = _shared_team_project(client)
    if to == "stranger":
        _register(client, to)

    r = offer_project(client, p["id"], to, by="alice")

    assert r.status_code == 422, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_a_mentor_is_not_offered_the_project(client):
    """A mentor looks on; the project is offered to someone who works in it."""
    p = _project(client, owner="alice")
    add_external_member(client, p["id"], "dana", by="alice")
    made = client.put(
        f"/projects/{p['id']}/members/dana/role",
        json={"role": "mentor"},
        headers=session_auth_headers("alice"),
    )
    assert made.status_code == 200, made.text

    r = offer_project(client, p["id"], "dana", by="alice")

    assert r.status_code == 422, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_a_team_admin_cannot_take_the_project(client):
    """Handing over is the owner's alone: an admin of the project's team manages
    it, but does not make themselves its owner."""
    p = _shared_team_project(client)
    _add_member(client, p["id"], "dana", admin=True)

    r = offer_project(client, p["id"], "dana", by="dana")

    assert r.status_code == 403, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_a_plain_member_cannot_take_the_project(client):
    p = _shared_team_project(client)
    _add_member(client, p["id"], "erin")

    r = offer_project(client, p["id"], "erin", by="erin")

    assert r.status_code == 403, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_an_outsider_cannot_take_the_project(client):
    p = _project(client, owner="alice")

    r = offer_project(client, p["id"], "mallory", by="mallory")

    # Someone not in the project learns nothing about it.
    assert r.status_code == 404, r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_an_anonymous_caller_cannot_take_the_project(client):
    p = _project(client, owner="alice")

    r = client.post(f"/projects/{p['id']}/transfer", json={"to_handle": "mallory"})

    assert r.status_code in (401, 404), r.text
    assert _owner_of(client, p["id"]) == "alice"


def test_an_empty_handle_is_rejected_rather_than_blanking_the_owner(client):
    """`owner_handle = ""` is exactly the NULL-ish state #315 escaped — the
    write path must not be a way back into it."""
    p = _project(client, owner="alice")

    r = offer_project(client, p["id"], "   ", by="alice")

    assert r.status_code == 422
    assert _owner_of(client, p["id"]) == "alice"


def test_a_missing_project_is_a_404_not_a_500(client):
    r = offer_project(client, str(uuid.uuid4()), "bob", by="alice")
    assert r.status_code == 404


def test_creating_a_project_without_a_real_owner_is_refused(client):
    """#315: a project with no owner who is a person had nothing for its
    authority to stand on. The owner is whoever is signed in, so a request
    that names nobody has no owner to give the project and is refused rather
    than made ownerless."""
    r = client.post("/projects", json={"name": "无主项目"})

    assert r.status_code == 401, r.text
