"""任务默认 reviewer (#718 设置表)：派活时定下，递卡时沿用，显式指定优先。

The setting existed and nothing read it, so a project could configure a default
reviewer and every card still had to be routed by hand. These go through the
real doors — `POST /topics/{id}/split` and `POST /topics/{id}/accept-card` — and
read the answer off the card, because the interesting part is precisely that a
value travels from one door to the other.

Who resolves it, and when, is the load-bearing decision: the reviewer is written
onto the WORK when the work is dispatched, not read out of the project setting
when the card is filed. A setting is a policy that can change; who a piece of
work was handed to is a fact about the moment it was handed over.
"""

import itertools
import uuid

from tests.integration.conftest import session_auth_headers
from tests.integration.test_project_tree import _insert_block
from tests.machine_work import machine_commits

_written = itertools.count()


def _project(client) -> str:
    client.headers.update(session_auth_headers("alice"))
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _default_reviewer(client, pid: str, handle: str) -> None:
    r = client.put(
        f"/projects/{pid}/branch-protection", json={"default_reviewer": handle}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["default_reviewer"] == handle


def _room(client, pid: str) -> str:
    r = client.post(
        "/topics", json={"project_id": pid, "title": "房间", "created_by": "alice"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _split(client, room: str, title: str, reviewer: str | None = None) -> dict:
    body: dict = {"title": title}
    if reviewer is not None:
        body["reviewer_handle"] = reviewer
    r = client.post(f"/topics/{room}/split", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _file(client, pid: str, room: str, task_id: str, subject: str, **kw):
    """真的写点东西再递卡 —— 一次没有代码的交付不是交付。"""
    nth = next(_written)
    machine_commits(uuid.UUID(pid), uuid.UUID(task_id), {f"work-{nth}.txt": subject})
    body: dict = {"change_subject": subject, "routing_reason": "最懂"}
    body.update(kw)
    return client.post(f"/topics/{room}/tasks/{task_id}/accept-card", json=body)


# --- 派活 -----------------------------------------------------------------


def test_dispatching_work_without_naming_anybody_uses_the_project_default(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)

    task = _split(client, room, "一条活")

    assert task["reviewer_handle"] == "bob"


def test_naming_a_reviewer_when_dispatching_beats_the_project_default(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)

    task = _split(client, room, "这条给别人验", reviewer="carol")

    assert task["reviewer_handle"] == "carol"


def test_dispatch_requires_a_reviewer_before_creating_work(client):
    pid = _project(client)
    room = _room(client, pid)
    response = client.post(f"/topics/{room}/split", json={"title": "Needs reviewer"})
    assert response.status_code == 422
    assert "默认验收人" in response.json()["message"]
    assert client.get(f"/topics/{room}/tasks").json()["data"]["total"] == 0


def test_upgrading_discussion_requires_and_freezes_the_reviewer(client):
    pid = _project(client)
    room = _room(client, pid)
    block = _insert_block(client, pid, room, "Implement the import")
    response = client.post(f"/blocks/{block}/upgrade", json={})
    assert response.status_code == 422
    assert client.get(f"/topics/{room}/tasks").json()["data"]["total"] == 0
    _default_reviewer(client, pid, "bob")
    response = client.post(f"/blocks/{block}/upgrade", json={})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["reviewer_handle"] == "bob"
    _default_reviewer(client, pid, "carol")
    repeat = client.post(f"/blocks/{block}/upgrade", json={})
    assert repeat.json()["data"]["reviewer_handle"] == "bob"


# --- 派活 → 递卡（端到端） -------------------------------------------------


def test_the_card_goes_to_whoever_the_work_was_dispatched_to(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _split(client, room, "一条活")

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_naming_a_reviewer_on_the_card_beats_the_one_the_work_carries(client):
    """递卡时显式指定优先。递卡的人知道设置和活都不知道的事：这次改动是什么，
    以及谁懂那一块。"""
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _split(client, room, "一条活")

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
    """设置在派活和交付之间改了，这批活还是交给当初接下它的人。

    重新读设置的话，一批在旧策略下派出去的活会被悄悄改判给另一个人，而卡面上
    看不出发生过这件事。"""
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _split(client, room, "一条活")
    _default_reviewer(client, pid, "erin")

    r = _file(client, pid, room, task["id"], "feat(x): deliver it")

    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_independent_tasks_keep_their_different_reviewers(client):
    pid = _project(client)
    room = _room(client, pid)
    mine = _split(client, room, "Mine", reviewer="bob")
    theirs = _split(client, room, "Theirs", reviewer="carol")
    first = _file(client, pid, room, mine["id"], "feat: first task")
    second = _file(client, pid, room, theirs["id"], "feat: second task")
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["reviewer_handle"] == "bob"
    assert second.json()["data"]["reviewer_handle"] == "carol"
    assert first.json()["data"]["task_id"] == mine["id"]
    assert second.json()["data"]["task_id"] == theirs["id"]
