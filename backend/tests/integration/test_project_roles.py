"""Roles in a project and what each may do (#3830).

The rules, stated before they were written:

- a mentor reads the project and speaks in it, but changes nothing in it;
- only the owner makes someone an admin, and an admin does not move another
  admin;
- a project may take a permission away from a role, and then that role is
  refused it;
- managing people and roles stays the admins' whatever the project sets;
- an AI teammate never changes anyone's role.
"""

from tests.integration.conftest import (
    _register,
    join_project_team,
    new_project,
    post_message,
    room_agent_headers,
    session_auth_headers,
)


def _invite(client, pid: str, handle: str, *, role: str, by: str = "alice") -> None:
    _register(client, handle)
    inv = client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": handle, "role": role},
        headers=session_auth_headers(by),
    )
    assert inv.status_code == 200, inv.text
    ok = client.post(
        f"/invitations/{inv.json()['data']['id']}/respond",
        json={"accept": True},
        headers=session_auth_headers(handle),
    )
    assert ok.status_code == 200, ok.text


def _set_role(client, pid: str, handle: str, role: str, *, by: str):
    return client.put(
        f"/projects/{pid}/members/{handle}/role",
        json={"role": role},
        headers=session_auth_headers(by),
    )


def test_a_mentor_reads_and_speaks_but_changes_nothing(client):
    p = new_project(client, owner="alice")
    _invite(client, p["id"], "teacher", role="mentor")
    teacher = session_auth_headers("teacher")

    assert client.get(f"/projects/{p['id']}", headers=teacher).status_code == 200
    post_message(client, p["root_topic_id"], "teacher", {"content": "看了，方向没问题"})
    made = client.post(
        f"/projects/{p['id']}/documents",
        json={"title": "导师写的"},
        headers=teacher,
    )
    assert made.status_code == 403, made.text


def test_only_the_owner_makes_someone_an_admin(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    join_project_team(client, p["id"], "carol")

    assert _set_role(client, p["id"], "bob", "admin", by="alice").status_code == 200
    refused = _set_role(client, p["id"], "carol", "admin", by="bob")
    assert refused.status_code == 403, refused.text
    # An admin still moves people below them.
    assert _set_role(client, p["id"], "carol", "mentor", by="bob").status_code == 200


def test_an_admin_does_not_move_another_admin(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    join_project_team(client, p["id"], "carol")
    assert _set_role(client, p["id"], "bob", "admin", by="alice").status_code == 200
    assert _set_role(client, p["id"], "carol", "admin", by="alice").status_code == 200

    refused = _set_role(client, p["id"], "carol", "member", by="bob")
    assert refused.status_code == 403, refused.text
    removed = client.delete(
        f"/projects/{p['id']}/members/carol", headers=session_auth_headers("bob")
    )
    assert removed.status_code == 403, removed.text


def test_a_role_the_project_took_a_permission_from_is_refused_it(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    grants = client.get(
        f"/projects/{p['id']}/roles", headers=session_auth_headers("alice")
    ).json()["data"]["grants"]
    without = [g for g in grants["member"] if g != "create_channels"]
    changed = client.put(
        f"/projects/{p['id']}/roles",
        json={"grants": {"member": without}},
        headers=session_auth_headers("alice"),
    )
    assert changed.status_code == 200, changed.text

    refused = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "新频道"},
        headers=session_auth_headers("bob"),
    )
    assert refused.status_code == 403, refused.text


def test_managing_people_stays_the_admins(client):
    p = new_project(client, owner="alice")
    changed = client.put(
        f"/projects/{p['id']}/roles",
        json={"grants": {"admin": [], "member": ["manage_members", "change_roles"]}},
        headers=session_auth_headers("alice"),
    )
    assert changed.status_code == 200, changed.text

    grants = changed.json()["data"]["grants"]
    assert {"manage_members", "change_roles"} <= set(grants["admin"])
    assert not {"manage_members", "change_roles"} & set(grants["member"])


def test_an_ai_teammate_does_not_change_roles(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")

    refused = client.put(
        f"/projects/{p['id']}/members/bob/role",
        json={"role": "mentor"},
        headers=room_agent_headers(client, p["root_topic_id"]),
    )
    assert refused.status_code in (401, 403, 404), refused.text
    roster = client.get(
        f"/projects/{p['id']}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert next(r for r in roster if r["user_handle"] == "bob")["role"] == "member"


def test_the_project_list_says_where_i_sit(client):
    """The sidebar decides between 转让项目 and 退出项目 from the list it
    already has, so each listed project says where the caller sits."""
    p = new_project(client, owner="alice")
    _invite(client, p["id"], "teacher", role="mentor")

    def listed(handle: str) -> dict:
        rows = client.get("/projects", headers=session_auth_headers(handle)).json()[
            "data"
        ]["data"]
        return next(r for r in rows if r["id"] == p["id"])

    assert listed("alice")["my_role"] == "owner"
    mine = listed("teacher")
    assert mine["my_role"] == "mentor"
    assert "edit_content" not in mine["my_permissions"]
