"""「存成模板」收下来的那一条：项目的一份，谁读得到项目就读得到。

一条模板不是规则：没有谁在跑、没有执行记录、删掉不影响任何人在跑的东西，所以它
不挂在房间上（同一个项目的别的房间也该看到同一份），也没有 `can_delete` 这一档 ——
权限收窄的是「把规则从房间里拿走」，模板没这个重量。

跟着项目走：同一个人换台机器还在（此前它只在浏览器里），项目里别的人也看得到。
"""

import uuid

from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

OWNER = "user-1"
PERSON = session_auth_headers(OWNER)
MEMBER = "user-2"
STRANGER = "user-5"

WEEKLY = {
    "title": "每周进展汇总",
    "goal": "每周一把各频道上周完成的事汇总成一页",
    "note": "写成三五条",
    "trigger": "schedule",
    "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
}


def _project(client) -> str:
    r = post_project(client, json={"name": f"模板-{uuid.uuid4().hex[:6]}"}, owner=OWNER)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _mine(client, project, headers=PERSON) -> list[dict]:
    r = client.get(f"/projects/{project}/routine-templates", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _save(client, project, body=None, headers=PERSON):
    return client.post(
        f"/projects/{project}/routine-templates", json=body or WEEKLY, headers=headers
    )


def _change(client, template, body, headers=PERSON):
    """改这一条模板本身（模板页那一行上的「修改」然后「保存」）。"""
    return client.patch(f"/routine-templates/{template}", json=body, headers=headers)


def test_a_saved_template_comes_back_with_the_fields_the_form_asks_for(client):
    """存下去什么，读回来就是那几格 —— 名字、目标、补充说明、触发方式、时间。"""
    project = _project(client)
    made = _save(client, project)
    assert made.status_code == 200, made.text
    row = made.json()["data"]
    assert row["title"] == WEEKLY["title"]
    assert row["goal"] == WEEKLY["goal"]
    assert row["note"] == WEEKLY["note"]
    assert row["trigger"] == "schedule"
    assert row["spec"] == {"freq": "weekly", "time": "09:00", "weekdays": [0]}
    # 存下来的是这几格，不多不少：没有主人、没有状态、没有执行记录。
    assert "owner_handle" not in row and "state" not in row and "runs" not in row

    rows = _mine(client, project)
    assert [r["id"] for r in rows] == [row["id"]]


def test_a_name_left_empty_comes_from_the_goal(client):
    """名字可以省，省了就和一条规则一样从目标的第一句里取。"""
    project = _project(client)
    body = {**WEEKLY, "title": "", "goal": "每周一把进展汇总成一页。写成三五条"}
    row = _save(client, project, body).json()["data"]
    assert row["title"] == "每周一把进展汇总成一页"


def test_a_template_without_a_goal_is_refused(client):
    project = _project(client)
    refused = _save(client, project, {**WEEKLY, "goal": "   "})
    assert refused.status_code == 422, refused.text


def test_a_schedule_no_rule_could_be_made_from_is_refused_when_it_is_saved(client):
    """`spec` 走的是规则那一套校验：存进来的时候就说不行，而不是挑它的时候。"""
    project = _project(client)
    refused = _save(
        client,
        project,
        {**WEEKLY, "spec": {"freq": "weekly", "weekdays": [], "time": "09:00"}},
    )
    assert refused.status_code == 422, refused.text


def test_everyone_in_the_project_sees_the_same_list(client):
    """项目的一份：另一个成员（不在任何房间的名册上）也看得到、也存得下、也删得掉。"""
    project = _project(client)
    join_project_team(client, project, MEMBER)
    made = _save(client, project).json()["data"]

    member = session_auth_headers(MEMBER)
    assert [r["id"] for r in _mine(client, project, member)] == [made["id"]]
    theirs = _save(
        client,
        project,
        {**WEEKLY, "title": "我加的", "goal": "每月一号把上月的决定收成一页"},
        headers=member,
    )
    assert theirs.status_code == 200, theirs.text
    assert len(_mine(client, project)) == 2

    removed = client.delete(f"/routine-templates/{made['id']}", headers=member)
    assert removed.status_code == 200, removed.text
    assert [r["id"] for r in _mine(client, project)] == [theirs.json()["data"]["id"]]


def test_a_stranger_sees_nothing_and_saves_nothing(client):
    project = _project(client)
    made = _save(client, project).json()["data"]
    stranger = session_auth_headers(STRANGER)

    assert client.get(
        f"/projects/{project}/routine-templates", headers=stranger
    ).status_code in (401, 403)
    assert _save(client, project, headers=stranger).status_code in (401, 403)
    assert client.delete(
        f"/routine-templates/{made['id']}", headers=stranger
    ).status_code in (
        401,
        403,
    )


def test_templates_do_not_leak_between_projects(client):
    """A 项目里存的模板不该出现在 B 项目里。"""
    mine = _project(client)
    theirs = _project(client)
    _save(client, mine)
    assert _mine(client, theirs) == []


def test_a_saved_template_can_be_changed_in_place(client):
    """改这一条本身：还是那一行（id 不变），名字、目标、补充说明、什么时候跑都换上。"""
    project = _project(client)
    made = _save(client, project).json()["data"]

    changed = _change(
        client,
        made["id"],
        {
            "title": "每日进展汇总",
            "goal": "每天下班前把当天完成的事汇总成一页",
            "note": "",
            "trigger": "schedule",
            "spec": {"freq": "daily", "time": "18:00"},
        },
    )
    assert changed.status_code == 200, changed.text
    row = changed.json()["data"]
    assert row["id"] == made["id"]
    assert row["title"] == "每日进展汇总"
    assert row["goal"] == "每天下班前把当天完成的事汇总成一页"
    assert row["note"] == ""
    assert row["spec"] == {"freq": "daily", "time": "18:00"}
    # 改的是原来那一条，不是又存了一条。
    assert [r["id"] for r in _mine(client, project)] == [made["id"]]


def test_a_change_no_rule_could_be_made_from_is_refused_and_changes_nothing(client):
    """校验走的是同一套：改不改得动和存不存得下问的是同一件事，拒了就不该留下半份。"""
    project = _project(client)
    made = _save(client, project).json()["data"]

    refused = _change(
        client,
        made["id"],
        {**WEEKLY, "spec": {"freq": "weekly", "weekdays": [], "time": "09:00"}},
    )
    assert refused.status_code == 422, refused.text
    assert _mine(client, project)[0] == made


def test_a_change_that_loses_the_goal_is_refused(client):
    project = _project(client)
    made = _save(client, project).json()["data"]
    assert _change(client, made["id"], {**WEEKLY, "goal": "   "}).status_code == 422


def test_changing_a_name_left_empty_takes_it_from_the_goal_again(client):
    """改了目标、名字那一格又清空：名字跟着新目标的第一句走，不留着旧的。"""
    project = _project(client)
    made = _save(client, project).json()["data"]
    row = _change(
        client,
        made["id"],
        {**WEEKLY, "title": "", "goal": "每周一把进展汇总成一页。写成三五条"},
    ).json()["data"]
    assert row["title"] == "每周一把进展汇总成一页"


def test_the_whole_project_can_change_a_template_and_a_stranger_cannot(client):
    """项目的一份：同项目另一个人也改得动；外人一行都碰不到。"""
    project = _project(client)
    join_project_team(client, project, MEMBER)
    made = _save(client, project).json()["data"]

    member = session_auth_headers(MEMBER)
    theirs = _change(client, made["id"], {**WEEKLY, "title": "我改的"}, headers=member)
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["data"]["id"] == made["id"]

    stranger = session_auth_headers(STRANGER)
    outside = _change(
        client, made["id"], {**WEEKLY, "title": "外人改的"}, headers=stranger
    )
    assert outside.status_code in (401, 403)
    assert _mine(client, project)[0]["title"] == "我改的"


def test_changing_a_template_that_is_not_there_is_a_404(client):
    project = _project(client)
    _save(client, project)
    assert _change(client, uuid.uuid4(), WEEKLY).status_code == 404
