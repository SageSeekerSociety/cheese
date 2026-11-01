"""A person's 芝士 runs the spaces and projects the person manages, as the
person would on the settings pages: a space's name, its categories and invite
codes, announcements to everyone in it, the problems waiting for review; a
project's naming and a channel's manager. What reaches everyone, and what is
taken away, waits for the person by their default rules."""

import time
import uuid

from tests.integration.conftest import post_project
from tests.integration.test_assistant_agent import _acting, _as, _asked_in
from tests.integration.test_assistant_answers_for_a_team import (
    _bearer,
    _do,
    _read,
    _space,
)
from tests.support import assistant_actions


def _act(client, handle: str, said: str, act: dict) -> dict:
    token = _acting(client, handle, _asked_in(client, handle, said))
    return assistant_actions.review(client, token, act)


def _space_row(client, space: int) -> dict:
    got = client.get(f"/spaces/{space}", headers=_bearer(client, "alice"))
    return got.json()["data"]["space"]


def test_a_space_is_renamed_and_its_categories_kept(client, monkeypatch):
    space = _space(client, "alice")
    where = f"spaces/{space}"

    _do(
        client,
        monkeypatch,
        "alice",
        "把空间改名叫 操作系统 2026 秋",
        {
            "kind": "space_set",
            "where": where,
            "key": "name",
            "value": "操作系统 2026 秋",
        },
    )
    _do(
        client,
        monkeypatch,
        "alice",
        "加一个分类叫 期中项目",
        {"kind": "category", "where": where, "title": "期中项目"},
    )

    assert _space_row(client, space)["name"] == "操作系统 2026 秋"
    _, desk = _read(client, "alice", f"{where}/manage")
    assert "名称：操作系统 2026 秋" in desk
    assert "期中项目" in desk


def test_an_announcement_to_everyone_waits_for_the_person(client):
    space = _space(client, "alice")

    got = _act(
        client,
        "alice",
        "发个公告说周五停课",
        {
            "kind": "announce",
            "where": f"spaces/{space}",
            "title": "周五停课",
            "text": "本周五的课停一次。",
        },
    )

    assert got["allowed"] is False
    assert "发给所有人的消息" in got["reason"]
    listed = client.get(
        f"/spaces/{space}/announcements", headers=_bearer(client, "alice")
    ).json()["data"]
    assert listed["current"] == []


def test_an_invite_code_is_made_and_stopping_it_waits_for_the_person(
    client, monkeypatch
):
    space = _space(client, "alice")
    where = f"spaces/{space}"
    before = client.get(
        f"/spaces/{space}/invite-codes", headers=_bearer(client, "alice")
    ).json()["data"]["inviteCodes"]

    _do(
        client,
        monkeypatch,
        "alice",
        "给助教新建一个邀请码，用 3 次",
        {"kind": "invite_code", "where": where, "max_uses": 3, "text": "助教"},
    )
    codes = client.get(
        f"/spaces/{space}/invite-codes", headers=_bearer(client, "alice")
    ).json()["data"]["inviteCodes"]
    [made] = [c for c in codes if c["id"] not in {b["id"] for b in before}]
    assert made["maxUses"] == 3 and made["note"] == "助教"

    stop = _act(
        client,
        "alice",
        "把给助教的那个邀请码停掉",
        {"kind": "invite_code", "where": where, "ref": str(made["id"]), "remove": True},
    )
    assert stop["allowed"] is False
    assert "删除内容" in stop["reason"]


def test_a_problem_waiting_for_review_is_passed_for_the_person(client, monkeypatch):
    space = _space(client, "alice")
    category = _space_row(client, space).get("defaultCategoryId")
    published = client.post(
        "/tasks",
        json={
            "mentors": [],
            "name": "实现一个调度器",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": space,
            "categoryId": category,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_bearer(client, "alice"),
    )
    assert published.status_code == 200, published.text
    task = published.json()["data"]["task"]["id"]
    _, desk = _read(client, "alice", f"spaces/{space}/manage")
    assert f"spaces/{space}/tasks/{task}" in desk

    _do(
        client,
        monkeypatch,
        "alice",
        "把待审核的调度器那道题通过",
        {
            "kind": "problem_review",
            "ref": f"spaces/{space}/tasks/{task}",
            "choice": "accept",
        },
    )

    got = client.get(f"/tasks/{task}", headers=_bearer(client, "alice"))
    assert got.json()["data"]["task"]["approved"] == "APPROVED"


def test_someone_who_does_not_run_the_space_is_told_it_is_not_there(client):
    space = _space(client, "alice")
    token = _acting(client, "mallory", _asked_in(client, "mallory", "把这个空间改个名"))

    got = client.post(
        "/assistant/agent/review",
        json={
            "kind": "space_set",
            "where": f"spaces/{space}",
            "key": "name",
            "value": "x",
        },
        headers=_as(token),
    )

    assert got.status_code == 404
    assert _space_row(client, space)["name"] != "x"


def test_a_projects_task_naming_is_changed_for_the_person(client, monkeypatch):
    made = post_project(
        client,
        json={"name": f"P{uuid.uuid4().hex[:4]}"},
        headers=_bearer(client, "alice"),
    )
    project = made.json()["data"]
    slug = project["slug"]

    _do(
        client,
        monkeypatch,
        "alice",
        "任务改成手动命名",
        {"kind": "project_set", "where": slug, "key": "task_naming", "value": "manual"},
    )

    got = client.get(
        f"/projects/{project['id']}/task-naming", headers=_bearer(client, "alice")
    )
    assert got.json()["data"]["mode"] == "manual"
    _, said = _read(client, "alice", f"{slug}/settings")
    assert "手动命名" in said
