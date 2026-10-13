"""任务默认 reviewer (#718 设置表)：开始任务时定下，递卡时沿用，显式指定优先。

The setting existed and nothing read it, so a project could configure a default
reviewer and every card still had to be routed by hand. These go through the
real doors — `POST /topics/{task}/start` and
`POST /topics/{task}/accept-card` — and read the answer off the
card, because the interesting part is precisely that a value travels from one
door to the other.

Who resolves it, and when, is the load-bearing decision: the reviewer is written
onto the TASK when it is started, not read out of the project setting when the
card is filed. A setting is a policy that can change; who a piece of work was
handed to is a fact about the moment it was handed over.
"""

import itertools
import uuid

from tests.conftest import wait_work_idle
from tests.delivery import delivery_artifact
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    session_auth_headers,
)
from tests.machine_work import declare_task, machine_commits

_written = itertools.count()


def _project(client) -> str:
    client.headers.update(session_auth_headers("alice"))
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    # 2026-09-27: 递卡与改派那道门现在先问「这个人在不在房间里」
    # (`_require_reviewer_in_room`)。这里的 bob / carol / dave 是「被指派的审阅
    # 人」—— 让他们像真实参与者一样在项目里，量到的才是默认审阅人那条梯子本身。
    for handle in ("bob", "carol", "dave"):
        join_project_team(client, pid, handle)
    return pid


def _default_reviewer(client, pid: str, handle: str) -> None:
    r = client.put(
        f"/projects/{pid}/branch-protection", json={"default_reviewer": handle}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["default_reviewer"] == handle


def _room(client, pid: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _started(client, room: str, title: str, reviewer: str | None = None) -> dict:
    """alice creates a task and starts it, naming ``reviewer`` or nobody."""
    task = open_task(client, room, title, reviewer=reviewer)
    wait_work_idle()
    return task


def _file(client, pid: str, room: str, task_id: str, subject: str, **kw):
    """真的写点东西再递卡 —— 一次没有代码的交付不是交付。"""
    nth = next(_written)
    declare_task(uuid.UUID(pid), uuid.UUID(task_id))
    machine_commits(uuid.UUID(pid), uuid.UUID(task_id), {f"work-{nth}.txt": subject})
    body: dict = {
        "change_subject": subject,
        "focus": "最懂",
        **delivery_artifact(client, room),
    }
    body.update(kw)
    return client.post(f"/topics/{task_id}/accept-card", json=body)


# --- 开始 -----------------------------------------------------------------


def test_starting_a_task_without_naming_anybody_uses_the_project_default(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)

    task = _started(client, room, "一条活")

    assert task["reviewer_handle"] == "bob"


def test_naming_a_reviewer_when_starting_beats_the_project_default(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)

    task = _started(client, room, "这条给别人验", reviewer="carol")

    assert task["reviewer_handle"] == "carol"


# --- 开始 → 递卡（端到端） -------------------------------------------------


def test_the_card_goes_to_whoever_the_task_was_started_with(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _started(client, room, "一条活")

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_naming_a_reviewer_on_the_card_beats_the_one_the_work_carries(client):
    """递卡时显式指定优先。递卡的人知道设置和活都不知道的事：这次改动是什么，
    以及谁懂那一块。"""
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _started(client, room, "一条活")

    r = _file(
        client,
        pid,
        room,
        task["id"],
        "feat(x): deliver it",
        reviewer_handle="dave",
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "dave"


def test_the_card_keeps_the_reviewer_the_work_got_when_the_setting_changes(client):
    """设置在开始和交付之间改了，这批活还是交给当初接下它的人。

    重新读设置的话，一批在旧策略下开始的活会被悄悄改判给另一个人，而卡面上
    看不出发生过这件事。"""
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _started(client, room, "一条活")
    _default_reviewer(client, pid, "erin")

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_independent_tasks_keep_their_different_reviewers(client):
    pid = _project(client)
    room = _room(client, pid)
    mine = _started(client, room, "Mine", reviewer="bob")
    theirs = _started(client, room, "Theirs", reviewer="carol")
    first = _file(client, pid, room, mine["id"], "feat: first task")
    second = _file(client, pid, room, theirs["id"], "feat: second task")
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["reviewer_handle"] == "bob"
    assert second.json()["data"]["reviewer_handle"] == "carol"
    assert first.json()["data"]["task_id"] == mine["id"]
    assert second.json()["data"]["task_id"] == theirs["id"]
