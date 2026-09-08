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


def _file(client, pid: str, room: str, subject: str, **kw):
    """真的写点东西再递卡 —— 一次没有代码的交付不是交付。"""
    nth = next(_written)
    machine_commits(uuid.UUID(pid), uuid.UUID(room), {f"work-{nth}.txt": subject})
    body: dict = {"change_subject": subject, "routing_reason": "最懂"}
    body.update(kw)
    return client.post(f"/topics/{room}/accept-card", json=body)


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


def test_dispatching_work_in_a_project_with_no_default_names_nobody(client):
    """空着是合法结果，不是错误：递卡时点名就是了。为它拒绝派活，等于一个没配
    过这项设置的项目连活都派不出去。"""
    pid = _project(client)
    room = _room(client, pid)

    task = _split(client, room, "一条活")

    assert task["reviewer_handle"] is None


# --- 派活 → 递卡（端到端） -------------------------------------------------


def test_the_card_goes_to_whoever_the_work_was_dispatched_to(client):
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    task = _split(client, room, "一条活")

    r = _file(client, pid, room, "feat(x): deliver it", task_ids=[task["id"]])

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
        "feat(x): deliver it",
        task_ids=[task["id"]],
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

    r = _file(client, pid, room, "feat(x): deliver it", task_ids=[task["id"]])

    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_a_delivery_that_declares_nothing_still_finds_the_batchs_reviewer(client):
    """没带 `--task` 的交付照样有验收人：这一批上的活是能问的最好答案。"""
    pid = _project(client)
    _default_reviewer(client, pid, "bob")
    room = _room(client, pid)
    _split(client, room, "一条活", reviewer="carol")

    r = _file(client, pid, room, "feat(x): deliver it")

    assert r.json()["data"]["reviewer_handle"] == "carol"


def test_work_handed_to_two_different_people_refuses_to_pick_one(client):
    """两条活交给了两个人，一起交付 —— 「谁说这次可以合」是个真问题，平台编一个
    答案就是把某个人的验收派给了另一个人，而且看起来毫无破绽。"""
    pid = _project(client)
    room = _room(client, pid)
    mine = _split(client, room, "我的活", reviewer="bob")
    theirs = _split(client, room, "他的活", reviewer="carol")

    r = _file(
        client, pid, room, "feat(x): deliver both", task_ids=[mine["id"], theirs["id"]]
    )

    assert r.status_code == 422, r.text
    message = r.json()["message"]
    assert "bob" in message and "carol" in message

    # 点名一个就过去了。
    ok = _file(
        client,
        pid,
        room,
        "feat(x): deliver both",
        task_ids=[mine["id"], theirs["id"]],
        reviewer_handle="bob",
    )
    assert ok.status_code == 200, ok.text


def test_nobody_anywhere_is_refused_with_a_sentence_that_says_what_to_do(client):
    """既没点名、活上也没有、项目也没设 —— 这里不能猜。猜出来的验收人会让一张
    卡看起来路由正确地躺在一个从没答应看它的人那里。"""
    pid = _project(client)
    room = _room(client, pid)

    r = _file(client, pid, room, "feat(x): deliver it")

    assert r.status_code == 422, r.text
    message = r.json()["message"]
    assert "默认验收人" in message
