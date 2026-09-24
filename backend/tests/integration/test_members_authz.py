"""越权: who may bring a person into a project, or take one out.

A person is in a project because they are on its team, or because they accepted
an invitation as an external member. So the writes guarded here are sending an
invitation and removing an external member, and both need someone who manages
the project: its owner, or an owner/admin of its team. A person cannot be put on
the roster directly at all — only an AI teammate's seat goes in that way.
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    new_project,
)


def _invite(client, pid: str, handle: str, **kw):
    return client.post(
        f"/projects/{pid}/invitations", json={"user_handle": handle}, **kw
    )


def _external(client, pid: str) -> list[str]:
    rows = client.get(f"/projects/{pid}/members").json()["data"]["data"]
    return [m["user_handle"] for m in rows if m.get("source") == "external"]


def test_owner_invites_and_an_accepted_invitation_makes_an_external_member(client):
    pid = new_project(client, owner="alice")["id"]
    add_external_member(client, pid, "bob", by="alice")
    assert _external(client, pid) == ["bob"]


def test_nobody_without_standing_can_invite(client, bearer):
    """The reported hole: no credential at all used to be enough."""
    pid = new_project(client, owner="alice")["id"]
    join_project_team(client, pid, "tess")

    assert _invite(client, pid, "mallory").status_code == 403
    assert _invite(client, pid, "mallory", headers=bearer("mallory")).status_code == 403
    # Belonging to the team is not managing its projects.
    assert _invite(client, pid, "carol", headers=bearer("tess")).status_code == 403


def test_a_team_admin_manages_external_members(client, bearer):
    pid = new_project(client, owner="alice")["id"]
    join_project_team(client, pid, "dana", admin=True)

    add_external_member(client, pid, "carol", by="dana")
    assert _external(client, pid) == ["carol"]
    r = client.delete(f"/projects/{pid}/members/carol", headers=bearer("dana"))
    assert r.status_code == 200
    assert _external(client, pid) == []


def test_a_person_is_never_put_on_the_roster_directly(client, bearer):
    """Even the owner: a person comes in by accepting an invitation."""
    pid = new_project(client, owner="alice")["id"]
    r = client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "bob"},
        headers=bearer("alice"),
    )
    assert r.status_code == 422
    assert _external(client, pid) == []


def test_token_wins_over_a_forged_body_handle(client, bearer):
    """The actor comes from the verified token only — a body field naming the
    owner is ignored."""
    pid = new_project(client, owner="alice")["id"]
    forged = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": "mallory", "actor": "alice"},
        headers=bearer("mallory"),
    )
    assert forged.status_code == 403


def test_agent_scoped_token_cannot_invite_or_remove(client):
    """芝士 holding a valid per-turn token for THIS project is still refused."""
    pid = new_project(client, owner="alice")["id"]
    add_external_member(client, pid, "bob", by="alice")
    scoped = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=pid, agent_handle="unprivileged-agent"
        )
    }

    assert _invite(client, pid, "carol", headers=scoped).status_code == 403
    r = client.delete(f"/projects/{pid}/members/bob", headers=scoped)
    assert r.status_code == 403
    assert _external(client, pid) == ["bob"]


def test_removal_is_guarded(client, bearer):
    pid = new_project(client, owner="alice")["id"]
    add_external_member(client, pid, "bob", by="alice")

    assert (
        client.delete(
            f"/projects/{pid}/members/bob", headers=bearer("mallory")
        ).status_code
        == 403
    )
    assert client.delete(f"/projects/{pid}/members/bob").status_code == 403
    assert _external(client, pid) == ["bob"]


def test_reading_the_roster_stays_open(client, bearer):
    """Only the writes were closed: the UI reads this list without a token."""
    pid = new_project(client, owner="alice")["id"]
    assert client.get(f"/projects/{pid}/members").status_code == 200
