"""Roles in a project and what each may do (#3830, #4795).

The rules, stated before they were written:

- a mentor reads the project and speaks in it, but changes nothing in it;
- only the owner makes someone an admin, and an admin does not move another
  admin;
- a project may take a permission away from a role, and then that role is
  refused it;
- managing people and roles stays the admins' whatever the project sets;
- an AI teammate never changes anyone's role;
- a project's own role lets its holders do exactly what it grants;
- admin and member keep their names and cannot be deleted;
- a role someone holds is deleted only once they are moved to another;
- a project has one mentor role, made the first time it is asked for;
- a channel that lets only some roles speak refuses the others in its main
  line, and lets everyone answer in a 支线 only while 支线 are left open.
"""

from tests.integration.conftest import (
    _register,
    in_thread,
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


def _roles(client, pid: str, by: str = "alice") -> list[dict]:
    r = client.get(f"/projects/{pid}/roles", headers=session_auth_headers(by))
    assert r.status_code == 200, r.text
    return r.json()["data"]["roles"]


def _role(client, pid: str, key: str) -> dict:
    return next(r for r in _roles(client, pid) if r["key"] == key)


def _new_role(client, pid: str, name: str, *, copy_from: str = "member") -> dict:
    r = client.post(
        f"/projects/{pid}/roles",
        json={"name": name, "copy_from": copy_from},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _grant(client, pid: str, key: str, permissions: list[str]):
    return client.patch(
        f"/projects/{pid}/roles/{key}",
        json={"permissions": permissions},
        headers=session_auth_headers("alice"),
    )


def _held(client, pid: str, handle: str) -> str:
    roster = client.get(
        f"/projects/{pid}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(r for r in roster if r["user_handle"] == handle)["role"]


def _makes_document(client, pid: str, handle: str) -> int:
    return client.post(
        f"/projects/{pid}/documents",
        json={"title": f"{handle} 写的"},
        headers=session_auth_headers(handle),
    ).status_code


def test_a_mentor_reads_and_speaks_but_changes_nothing(client):
    p = new_project(client, owner="alice")
    _invite(client, p["id"], "teacher", role="mentor")
    teacher = session_auth_headers("teacher")

    assert client.get(f"/projects/{p['id']}", headers=teacher).status_code == 200
    post_message(client, p["root_topic_id"], "teacher", {"content": "看了，方向没问题"})
    assert _makes_document(client, p["id"], "teacher") == 403


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
    member = _role(client, p["id"], "member")
    without = [g for g in member["permissions"] if g != "create_channels"]
    assert _grant(client, p["id"], "member", without).status_code == 200

    refused = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "新频道"},
        headers=session_auth_headers("bob"),
    )
    assert refused.status_code == 403, refused.text


def test_managing_people_stays_the_admins(client):
    p = new_project(client, owner="alice")
    assert _grant(client, p["id"], "admin", []).status_code == 200
    assert (
        _grant(
            client, p["id"], "member", ["manage_members", "change_roles"]
        ).status_code
        == 200
    )
    own = _new_role(client, p["id"], "助教")
    granted = _grant(client, p["id"], own["key"], ["manage_members", "speak"])
    assert granted.status_code == 200, granted.text

    assert {"manage_members", "change_roles"} <= set(
        _role(client, p["id"], "admin")["permissions"]
    )
    assert not {"manage_members", "change_roles"} & set(
        _role(client, p["id"], "member")["permissions"]
    )
    assert granted.json()["data"]["permissions"] == ["speak"]


def test_an_ai_teammate_does_not_change_roles(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")

    refused = client.put(
        f"/projects/{p['id']}/members/bob/role",
        json={"role": "mentor"},
        headers=room_agent_headers(client, p["root_topic_id"]),
    )
    assert refused.status_code in (401, 403, 404), refused.text
    assert _held(client, p["id"], "bob") == "member"


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
    assert mine["my_role"] == "member"
    assert mine["my_role_key"] == _held(client, p["id"], "teacher")
    assert "edit_content" not in mine["my_permissions"]


def test_a_projects_own_role_grants_exactly_what_it_says(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    tester = _new_role(client, p["id"], "内测用户", copy_from="member")
    assert (
        _grant(client, p["id"], tester["key"], ["speak", "comment"]).status_code == 200
    )
    assert (
        _set_role(client, p["id"], "bob", tester["key"], by="alice").status_code == 200
    )

    post_message(client, p["root_topic_id"], "bob", {"content": "试用了一下"})
    assert _makes_document(client, p["id"], "bob") == 403

    assert (
        _grant(
            client, p["id"], tester["key"], ["speak", "comment", "edit_content"]
        ).status_code
        == 200
    )
    assert _makes_document(client, p["id"], "bob") == 200
    assert _role(client, p["id"], tester["key"])["holders"] == 1


def test_admin_and_member_keep_their_names_and_stay(client):
    p = new_project(client, owner="alice")
    for key in ("admin", "member"):
        renamed = client.patch(
            f"/projects/{p['id']}/roles/{key}",
            json={"name": "改个名"},
            headers=session_auth_headers("alice"),
        )
        assert renamed.status_code == 422, renamed.text
        deleted = client.delete(
            f"/projects/{p['id']}/roles/{key}", headers=session_auth_headers("alice")
        )
        assert deleted.status_code == 422, deleted.text
    assert [r["key"] for r in _roles(client, p["id"])] == ["admin", "member"]


def test_a_held_role_is_deleted_only_after_its_people_move(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    tester = _new_role(client, p["id"], "内测用户")
    assert (
        _set_role(client, p["id"], "bob", tester["key"], by="alice").status_code == 200
    )

    refused = client.delete(
        f"/projects/{p['id']}/roles/{tester['key']}",
        headers=session_auth_headers("alice"),
    )
    assert refused.status_code == 422, refused.text
    assert _held(client, p["id"], "bob") == tester["key"]

    moved = client.delete(
        f"/projects/{p['id']}/roles/{tester['key']}",
        params={"move_to": "member"},
        headers=session_auth_headers("alice"),
    )
    assert moved.status_code == 200, moved.text
    assert _held(client, p["id"], "bob") == "member"
    assert tester["key"] not in [r["key"] for r in _roles(client, p["id"])]


def test_a_project_has_one_mentor_role_made_when_first_needed(client):
    p = new_project(client, owner="alice")
    assert not [r for r in _roles(client, p["id"]) if r["template"] == "mentor"]

    _invite(client, p["id"], "teacher", role="mentor")
    _invite(client, p["id"], "advisor", role="mentor")

    mentors = [r for r in _roles(client, p["id"]) if r["template"] == "mentor"]
    assert len(mentors) == 1
    assert mentors[0]["holders"] == 2
    assert _held(client, p["id"], "teacher") == mentors[0]["key"]


def _limit(client, pid_topic: str, roles: list[str] | None, *, threads: bool):
    r = client.put(
        f"/topics/{pid_topic}/speakers",
        json={"speaker_roles": roles, "thread_replies_open": threads},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def test_a_channel_limited_to_some_roles_refuses_the_rest_in_its_main_line(client):
    p = new_project(client, owner="alice")
    join_project_team(client, p["id"], "bob")
    join_project_team(client, p["id"], "carol")
    tester = _new_role(client, p["id"], "内测用户")
    assert (
        _set_role(client, p["id"], "carol", tester["key"], by="alice").status_code
        == 200
    )
    room = p["root_topic_id"]
    _limit(client, room, ["member"], threads=True)

    post_message(client, room, "bob", {"content": "今天上线了三项改动"})
    post_message(client, room, "alice", {"content": "所有者总能说话"})
    refused = client.post(
        f"/topics/{room}/messages",
        json={
            "request_id": "00000000-0000-0000-0000-000000000001",
            "content": "我也说",
        },
        headers=session_auth_headers("carol"),
    )
    assert refused.status_code == 403, refused.text

    thread = in_thread(client, room, "bob")
    post_message(client, thread, "carol", {"content": "收到"})

    _limit(client, room, ["member"], threads=False)
    closed = client.post(
        f"/topics/{thread}/messages",
        json={
            "request_id": "00000000-0000-0000-0000-000000000002",
            "content": "再问一句",
        },
        headers=session_auth_headers("carol"),
    )
    assert closed.status_code == 403, closed.text


def test_a_role_people_joined_by_invitation_can_still_be_deleted(client):
    """An invitation someone accepted is a record: it does not keep the role it
    brought from being deleted."""
    p = new_project(client, owner="alice")
    tester = _new_role(client, p["id"], "内测用户")
    _invite(client, p["id"], "zhao", role=tester["key"])
    assert _held(client, p["id"], "zhao") == tester["key"]

    moved = client.delete(
        f"/projects/{p['id']}/roles/{tester['key']}",
        params={"move_to": "member"},
        headers=session_auth_headers("alice"),
    )
    assert moved.status_code == 200, moved.text
    assert _held(client, p["id"], "zhao") == "member"
