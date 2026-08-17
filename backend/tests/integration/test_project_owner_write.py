"""PUT /projects/{id}/owner — the missing writer for `project.owner_handle` (#315).

The field had seven readers and one writer (`POST /projects`), so a project
that came into existence without an owner could never acquire one. That is not
hypothetical: on the dogfood project the column sat NULL for six days. Nothing
broke loudly — all seven readers fall back to `lead` — so the whole of the
project's owner-level authority silently collapsed onto the single person
holding that role, and nobody noticed until someone went looking.

These tests pin the way out and the two rails on it: only a steward may move
ownership, and it may only move to someone the roster already knows.
"""

import uuid

from tests.integration.conftest import session_auth_headers


def _project(client, owner: str | None = None) -> dict:
    body: dict = {"name": "P"}
    if owner is not None:
        body["owner_handle"] = owner
    r = client.post("/projects", json=body, headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _add_member(client, project_id: str, handle: str, role: str, *, actor: str) -> None:
    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": handle, "role": role},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200, r.text


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
    _add_member(client, p["id"], "bob", "member", actor="alice")

    r = _set_owner(client, p["id"], "bob", actor="alice")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["owner_handle"] == "bob"
    assert _owner_of(client, p["id"]) == "bob"


def test_a_lead_can_claim_a_project_that_has_no_owner(client):
    """The escape from #315's actual state: nobody holds the project, and the
    lead is the only rung of authority that still answers. Before this route
    there was no way out of that at all — the field was write-once at create."""
    p = _project(client, owner="alice")
    _add_member(client, p["id"], "dana", "lead", actor="alice")

    assert _set_owner(client, p["id"], "dana", actor="dana").status_code == 200
    assert _owner_of(client, p["id"]) == "dana"


def test_a_plain_member_cannot_take_the_project(client):
    p = _project(client, owner="alice")
    _add_member(client, p["id"], "erin", "member", actor="alice")

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


def test_ownership_cannot_be_handed_to_someone_off_the_roster(client):
    """An owner outside the roster is worse than no owner: `authorize_topic_access`
    reads the roster, so that owner cannot open the project's own topics."""
    p = _project(client, owner="alice")

    r = _set_owner(client, p["id"], "stranger", actor="alice")

    assert r.status_code == 422
    assert "成员" in r.json()["message"]
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


def test_creating_a_project_without_a_real_owner_says_so(client, caplog):
    """#315's third recommendation, corrected against the code as it stands.

    The issue said the owner could "silently become None". When #332 was written
    it couldn't — `resolve()` hands back the literal handle `anonymous`, which is
    the same hole wearing a value: it matches no user account, so all seven
    readers still collapse onto `lead`, while the column *looks* populated. So
    the warning fires on "not a real person", not on "empty".

    The stored value has since gone back to NULL, deliberately. `anonymous` in
    that column also jams the ownerless-room escape hatch, which opens only on an
    ABSENT owner and refuses to judge a handle by its name (nothing reserves that
    username, so an account could hold it). NULL is the honest value for "we do
    not know", and the warning below is what keeps it from being silent.
    """
    with caplog.at_level("WARNING", logger="cheesex.projects"):
        r = client.post("/projects", json={"name": "无主项目"})

    assert r.status_code == 200
    assert r.json()["data"]["owner_handle"] is None
    assert [rec for rec in caplog.records if "without a real owner" in rec.message]


def test_creating_a_project_with_a_real_owner_stays_quiet(client, caplog):
    """The warning has to mean something — an ordinary create must not trip it."""
    with caplog.at_level("WARNING", logger="cheesex.projects"):
        _project(client, owner="alice")

    assert not [rec for rec in caplog.records if "without a real owner" in rec.message]
