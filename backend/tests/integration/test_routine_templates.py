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
