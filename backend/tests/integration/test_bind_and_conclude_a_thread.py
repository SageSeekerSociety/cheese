"""房间开一条活、认领一个分身、验完货替它落结论——这条链上的三个端点。

Under 任务=分身 the platform stops raising a container per thread. `split` writes
the row and stops; the room spawns a worker in the session it already has and
says which one with `bind`; and when it has read what came back and satisfied
itself, it files the conclusion with `conclude`.

Every refusal here is one somebody would otherwise hit silently: an id that
names another room's work, a thread that already finished, a worker already
doing something else. None of those fail loudly on their own — they just
re-address a running worker's events and leave the first thread wondering where
its tool calls went.
"""

import uuid

from tests.integration.conftest import session_token


def _bearer(handle: str) -> dict:
    return {"Authorization": f"Bearer {session_token(handle)}"}


def _room(client, owner: str = "alice") -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    return p["id"], p["root_topic_id"]


def _split(client, room_id: str, owner: str = "alice", title: str = "一条活") -> dict:
    r = client.post(
        f"/topics/{room_id}/split",
        json={"title": title, "brief": "干这个"},
        headers=_bearer(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_fresh_thread_has_nobody_on_it(client):
    """split 只建行,不起人——「没人做」是正常状态,不是没派成功。"""
    _, room_id = _room(client)
    task = _split(client, room_id)

    assert task["subagent_id"] is None
    # 排队机制随着每条活一个容器一起走了:没有容器可排。
    assert "queued" not in task
    assert "slots_held_by" not in task


def test_binding_says_which_worker_is_on_it(client):
    _, room_id = _room(client)
    task = _split(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["subagent_id"] == "worker-1"

    listed = client.get(
        f"/topics/{room_id}/tasks", headers=_bearer("alice")
    ).json()["data"]["data"]
    assert [t["subagent_id"] for t in listed] == ["worker-1"]


def test_binding_the_same_worker_twice_is_refused(client):
    """一个分身同时只做一条活。

    Not a bookkeeping nicety: rebinding would not move the work, it would
    re-address a running worker's events, and the first thread would stop
    receiving its own tool calls without anything saying so.
    """
    _, room_id = _room(client)
    first = _split(client, room_id, title="活一")
    second = _split(client, room_id, title="活二")
    client.post(
        f"/topics/{room_id}/tasks/{first['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )

    r = client.post(
        f"/topics/{room_id}/tasks/{second['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 409
    assert "活一" in r.text


def test_rebinding_the_same_thread_to_the_same_worker_is_fine(client):
    """重发一次 bind 不该报错——同一条活、同一个分身,答案没变。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    for _ in range(2):
        r = client.post(
            f"/topics/{room_id}/tasks/{task['id']}/bind",
            json={"agent_id": "worker-1"},
            headers=_bearer("alice"),
        )
        assert r.status_code == 200, r.text


def test_another_rooms_thread_cannot_be_bound_here(client):
    _, room_a = _room(client)
    _, room_b = _room(client)
    task = _split(client, room_b)

    r = client.post(
        f"/topics/{room_a}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404


def test_a_finished_thread_cannot_be_bound(client):
    """收了的活不接新分身——接了,它的事件会被一条谁也不看的线吸走。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/conclude",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    card = client.get(
        f"/topics/{task['id']}/conclusion-cards", headers=_bearer("alice")
    ).json()["data"]["data"][0]
    settled = client.post(
        f"/topics/{room_id}/conclusion-cards/{card['id']}/accept",
        json={"decided_by": "alice"},
        headers=_bearer("alice"),
    )
    assert settled.status_code == 200, settled.text

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-2"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 409


def test_an_outsider_cannot_bind(client):
    _, room_id = _room(client)
    task = _split(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("mallory"),
    )
    assert r.status_code == 403


def test_a_thread_cannot_bind_or_conclude_itself(client):
    """认领和落结论都是房间的事:活自己说了不算,它就是被评价的那一方。"""
    _, room_id = _room(client)
    task = _split(client, room_id)

    for path, body in (
        (f"/topics/{task['id']}/tasks/{task['id']}/bind", {"agent_id": "w"}),
        (f"/topics/{task['id']}/tasks/{task['id']}/conclude", {"conclusion": "做完了"}),
    ):
        r = client.post(path, json=body, headers=_bearer("alice"))
        assert r.status_code in (403, 422), (path, r.status_code, r.text)


def test_concluding_a_thread_reaches_the_room_and_opens_a_card(client):
    """结论回流三件事照旧:房间线上一条消息、文档里一节、一张卡。"""
    _, room_id = _room(client)
    task = _split(client, room_id)
    client.post(
        f"/topics/{room_id}/tasks/{task['id']}/bind",
        json={"agent_id": "worker-1"},
        headers=_bearer("alice"),
    )

    r = client.post(
        f"/topics/{room_id}/tasks/{task['id']}/conclude",
        json={"conclusion": "分页改成 cursor，旧接口没动"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200, r.text
    assert "分页改成 cursor" in r.json()["data"]["content"]

    blocks = client.get(f"/topics/{room_id}/blocks", headers=_bearer("alice")).json()[
        "data"
    ]["data"]
    assert any("分页改成 cursor" in b["content"] for b in blocks)

    cards = client.get(
        f"/topics/{task['id']}/conclusion-cards", headers=_bearer("alice")
    ).json()["data"]["data"]
    assert [c["status"] for c in cards] == ["open"]


def test_concluding_a_thread_of_another_room_is_refused(client):
    _, room_a = _room(client)
    _, room_b = _room(client)
    task = _split(client, room_b)

    r = client.post(
        f"/topics/{room_a}/tasks/{task['id']}/conclude",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404


def test_concluding_something_that_is_not_a_thread_is_refused(client):
    _, room_id = _room(client)
    r = client.post(
        f"/topics/{room_id}/tasks/{uuid.uuid4()}/conclude",
        json={"conclusion": "做完了"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 404
