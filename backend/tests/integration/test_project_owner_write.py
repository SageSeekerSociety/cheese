"""PUT /projects/{id}/owner — the missing writer for `project.owner_handle` (#315).

The field had seven readers and one writer (`POST /projects`), so a project
that came into existence without an owner could never acquire one. That is not
hypothetical: on the dogfood project the column sat NULL for six days. Nothing
broke loudly — every reader falls back to someone else — so the project's
owner-level authority silently collapsed, and nobody noticed until someone went
looking.

These tests pin the way out and the two rails on it: only someone who manages
the project (its owner, or a team owner/admin) may move ownership, and it may
only move to someone on the project's team.
"""

import uuid

from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client, owner: str | None = None) -> dict:
    body: dict = {"name": "P"}
    if owner is not None:
        body["owner_handle"] = owner
    r = post_project(client, json=body, headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


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


def test_ownership_cannot_be_handed_to_someone_off_the_team(client):
    """An owner from outside the project's team is worse than no owner: they
    would own a project they cannot open."""
    p = _project(client, owner="alice")

    r = _set_owner(client, p["id"], "stranger", actor="alice")

    assert r.status_code == 422
    assert "成员" in r.json()["message"]
    assert _owner_of(client, p["id"]) == "alice"


def test_ownership_cannot_go_to_an_external_member(client):
    """An external member sees this one project and nothing else of the team;
    the project is the team's, so its owner is someone from the team."""
    p = _project(client, owner="alice")
    add_external_member(client, p["id"], "guest", by="alice")

    r = _set_owner(client, p["id"], "guest", actor="alice")

    assert r.status_code == 422
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
