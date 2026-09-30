"""Who may see a rule, and who may act on one.

看到一条周期任务 = 它所在房间名册上的人（含这个房间的 AI 队友）+ 项目管理员。
操作 = 规则主人（``owner_handle``）或项目管理员；其余人 403，芝士只能起草和修改。

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
    for action in ("confirm", "pause", "resume"):
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


def test_a_room_member_reads_the_rule_but_may_not_touch_it(client):
    project = _project(client)
    room = _room(client, project)
    _join(client, project, MEMBER)
    _seat(client, room, MEMBER)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    member = session_auth_headers(MEMBER)
    seen = client.get(f"/routines/{rule['id']}", headers=member)
    assert seen.status_code == 200, seen.text
    assert seen.json()["data"]["can_manage"] is False
    assert client.get(f"/routines/{rule['id']}/runs", headers=member).status_code == 200

    refused = client.post(f"/routines/{rule['id']}/pause", headers=member)
    assert refused.status_code == 403, refused.text
    assert OWNER in refused.json()["message"], refused.json()
    edited = client.patch(
        f"/routines/{rule['id']}", json={"title": "别人的规则"}, headers=member
    )
    assert edited.status_code == 403, edited.text


def test_the_owner_can_manage_their_own_rule(client):
    project = _project(client)
    room = _room(client, project)
    rule = _weekly(client, room, headers=PERSON).json()["data"]

    assert rule["can_manage"] is True
    assert (
        client.get(f"/routines/{rule['id']}", headers=PERSON).json()["data"][
            "can_manage"
        ]
        is True
    )
    assert (
        client.post(f"/routines/{rule['id']}/pause", headers=PERSON).status_code == 200
    )
    rows = _overview(client, project, PERSON)
    assert _rule_of(rows, rule["id"])["can_manage"] is True


def test_a_project_admin_manages_a_rule_of_a_room_they_are_not_in(client):
    project = _project(client)
    room = _room(client, project)
    _join(client, project, ADMIN, admin=True)
    rule = _weekly(client, room, headers=PERSON).json()["data"]
    admin = session_auth_headers(ADMIN)

    seen = client.get(f"/routines/{rule['id']}", headers=admin)
    assert seen.status_code == 200, seen.text
    assert seen.json()["data"]["can_manage"] is True
    assert _rule_of(_overview(client, project, admin), rule["id"])["can_manage"] is True

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


def test_a_teammate_drafting_for_a_person_leaves_the_buttons_to_that_person(client):
    """芝士起草的规则属于它替的那个人：按钮在他手上，不在芝士手上。"""
    project = _project(client)
    room = _room(client, project)
    drafted = _weekly(client, room, owner_handle=OWNER).json()["data"]
    assert drafted["state"] == "draft"
    assert drafted["can_manage"] is False, "芝士替人起草，按钮不该画给芝士"

    mine = client.get(f"/routines/{drafted['id']}", headers=PERSON).json()["data"]
    assert mine["can_manage"] is True
    confirmed = client.post(f"/routines/{drafted['id']}/confirm", headers=PERSON)
    assert confirmed.status_code == 200, confirmed.text
    # 芝士仍然能改：改了要重新确认。
    edited = client.patch(f"/routines/{drafted['id']}", json={"title": "改个名"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["state"] == "draft"
