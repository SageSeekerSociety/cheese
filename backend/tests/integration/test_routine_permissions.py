"""Who may see a rule, and who may act on one.

看到一条周期任务 = 它所在房间名册上的人（含这个房间的 AI 队友）+ 项目管理员。
改一条（改字段、暂停、恢复）= 看得到的人：规则是房间的，房间里的人都能收拾它。
删一条 = 只有它的主人（``owner_handle``）或项目管理员，而且只能是人：把规则从
房间里拿走是人的决定。

「项目成员」这一档故意不够：项目里别的房间的成员不该看见这个房间设了什么规则。
"""

from tests.integration.conftest import join_project_team, session_auth_headers
from tests.integration.test_routines import (
    OWNER,
    PERSON,
    _project,
    _room,
    _weekly,
)

ADMIN = "user-2"  # a project admin, on no room's roster
MEMBER = "user-3"  # on the project team and on ONE room's roster
OUTSIDER = "user-4"  # on the project team, on no room's roster
STRANGER = "user-5"  # not in the project at all


def _join(client, project, handle, *, admin=False):
    join_project_team(client, project, handle, admin=admin)


def _seat(client, room, handle, *, by=OWNER):
    r = client.post(
        f"/topics/{room}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text


def _overview(client, project, headers):
    r = client.get(f"/projects/{project}/routines", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _rule_of(rows, routine_id):
    return next(r for r in rows if r["id"] == routine_id)


def test_a_person_on_the_project_team_but_not_in_the_room_sees_nothing(client):
    """项目成员不够：规则是房间的，项目里别的房间的人看不到它。"""
    project = _project(client)
    room = _room(client, project)
    _join(client, project, OUTSIDER)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    outsider = session_auth_headers(OUTSIDER)
    assert client.get(f"/routines/{rule['id']}", headers=outsider).status_code == 403
    assert _overview(client, project, outsider) == []
    for action in ("pause", "resume"):
        r = client.post(f"/routines/{rule['id']}/{action}", headers=outsider)
        assert r.status_code == 403, (action, r.status_code)
    assert client.delete(f"/routines/{rule['id']}", headers=outsider).status_code == 403
    assert (
        client.patch(
            f"/routines/{rule['id']}", json={"title": "改别人的"}, headers=outsider
        ).status_code
        == 403
    )


def test_a_stranger_sees_nothing_at_all(client):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    stranger = session_auth_headers(STRANGER)

    assert client.get(f"/routines/{rule['id']}", headers=stranger).status_code in (
        401,
        403,
    )
    assert client.get(
        f"/projects/{project}/routines", headers=stranger
    ).status_code in (
        401,
        403,
    )


def test_a_room_member_may_change_a_rule_but_not_delete_it(client):
    """房间里的人收拾得了它；从房间里拿走是主人的事。"""
    project = _project(client)
    room = _room(client, project)
    _join(client, project, MEMBER)
    _seat(client, room, MEMBER)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    member = session_auth_headers(MEMBER)
    seen = client.get(f"/routines/{rule['id']}", headers=member)
    assert seen.status_code == 200, seen.text
    assert seen.json()["data"]["can_delete"] is False
    assert client.get(f"/routines/{rule['id']}/runs", headers=member).status_code == 200

    edited = client.patch(
        f"/routines/{rule['id']}", json={"instructions": "多写一条阻碍"}, headers=member
    )
    assert edited.status_code == 200, edited.text
    assert (
        client.post(f"/routines/{rule['id']}/pause", headers=member).status_code == 200
    )
    assert (
        client.post(f"/routines/{rule['id']}/resume", headers=member).status_code == 200
    )

    refused = client.delete(f"/routines/{rule['id']}", headers=member)
    assert refused.status_code == 403, refused.text
    assert OWNER in refused.json()["message"], refused.json()
    assert client.get(f"/routines/{rule['id']}", headers=member).status_code == 200


def test_the_owner_can_delete_their_own_rule(client):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    assert rule["can_delete"] is True
    assert (
        client.get(f"/routines/{rule['id']}", headers=PERSON).json()["data"][
            "can_delete"
        ]
        is True
    )
    rows = _overview(client, project, PERSON)
    assert _rule_of(rows, rule["id"])["can_delete"] is True
    assert client.delete(f"/routines/{rule['id']}", headers=PERSON).status_code == 200


def test_a_project_admin_may_delete_a_rule_of_a_room_they_are_not_in(client):
    project = _project(client)
    room = _room(client, project)
    _join(client, project, ADMIN, admin=True)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    admin = session_auth_headers(ADMIN)

    seen = client.get(f"/routines/{rule['id']}", headers=admin)
    assert seen.status_code == 200, seen.text
    assert seen.json()["data"]["can_delete"] is True
    assert _rule_of(_overview(client, project, admin), rule["id"])["can_delete"] is True

    assert (
        client.patch(
            f"/routines/{rule['id']}",
            json={"instructions": "管理员改过的"},
            headers=admin,
        ).status_code
        == 200
    )
    assert (
        client.post(f"/routines/{rule['id']}/pause", headers=admin).status_code == 200
    )
    assert (
        client.post(f"/routines/{rule['id']}/resume", headers=admin).status_code == 200
    )
    assert client.delete(f"/routines/{rule['id']}", headers=admin).status_code == 200


def test_the_project_overview_lists_only_the_rooms_the_caller_is_in(client):
    project = _project(client)
    mine = _room(client, project, "我在这")
    theirs = _room(client, project, "我不在")
    _join(client, project, MEMBER)
    _seat(client, mine, MEMBER)
    in_mine = _weekly(client, mine, headers=PERSON).json()["data"]
    in_theirs = _weekly(client, theirs, headers=PERSON).json()["data"]
    assert in_mine["id"] != in_theirs["id"]

    rows = _overview(client, project, session_auth_headers(MEMBER))
    assert [r["id"] for r in rows] == [in_mine["id"]]
    assert _rule_of(_overview(client, project, PERSON), in_theirs["id"])


def test_a_teammate_creating_for_a_person_leaves_deleting_to_that_person(client):
    """芝士替人建的规则属于它替的那个人：删除在他手上，不在芝士手上；改谁都能改。"""
    project = _project(client)
    room = _room(client, project)
    made = _weekly(client, room, owner_handle=OWNER).json()["data"]
    assert made["state"] == "active"
    assert made["can_delete"] is False, "芝士替人建的，删除不该画给芝士"

    mine = client.get(f"/routines/{made['id']}", headers=PERSON).json()["data"]
    assert mine["can_delete"] is True
    # 芝士仍然能改，改完立刻生效。
    edited = client.patch(f"/routines/{made['id']}", json={"title": "改个名"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["state"] == "active"
    refused = client.delete(f"/routines/{made['id']}")
    assert refused.status_code == 403, refused.text
