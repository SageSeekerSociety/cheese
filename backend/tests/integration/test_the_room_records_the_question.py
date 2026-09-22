"""The room keeps a record of what it was asked, and what it answered.

The panel above the composer is the live surface: a question appears there, you
answer it, and it goes away. What the room had no record of was that the
question was ever asked — so scroll-back could not say what 芝士 had been
stopped for, or who unstopped it.

记下来的那句话是**芝士自己问出口的**，所以它不是一条待读输入：它在等人回答，不
是在等自己读一遍。
"""

import time

import pytest

from app.domain.agent.remote_control import key
from tests.integration.conftest import session_auth_headers


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr("app.core.tokens._SECRET", "rc-record-test-key-at-least-32b")


@pytest.fixture
def place(client):
    project = client.post(
        "/projects", json={"name": "RC record", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "RC record", "created_by": "alice"},
    ).json()["data"]
    rows = client.get(f"/projects/{project['id']}/agents").json()["data"]["data"]
    (default,) = [row for row in rows if row["is_default"]]
    return project["id"], topic["id"], default["seat_handle"]


def _events(worker, uid):
    """A live question is a  event; the pending entry the room
    reads back is that payload."""
    return {
        "worker_epoch": worker["worker_epoch"],
        "events": [
            {
                "payload": {
                    "uuid": uid,
                    "type": "control_request",
                    "request_id": "ask-1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "Bash",
                        "input": {"command": "pwd"},
                    },
                }
            }
        ],
    }


def test_the_question_lands_in_the_room_once_and_the_answer_after_it(client, place):
    from app.api.routes.remote_control import store

    project, topic, agent = place

    # WHICH agent is asking: `rc_create` settles that when the session opens and
    # records it, and the question voiced into the room is signed with it.
    async def start():
        session = await store().create(
            {"p": project, "t": topic, "a": agent, "exp": int(time.time()) + 3600}, {}
        )
        return session["id"], await store().bridge(session)

    sid, worker = client.portal.call(start)
    headers = {"Authorization": f"Bearer {worker['worker_jwt']}"}
    try:
        first = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers=headers,
            json=_events(worker, "event-1"),
        )
        assert first.status_code == 200, first.text

        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        asked = [b for b in blocks if "Bash" in (b.get("content") or "")]
        assert len(asked) == 1, [b.get("content") for b in blocks]

        # The worker re-sends its pending list with every batch.
        again = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers=headers,
            json=_events(worker, "event-2"),
        )
        assert again.status_code == 200, again.text
        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert len([b for b in blocks if "Bash" in (b.get("content") or "")]) == 1

        answered = client.post(
            f"/topics/{topic}/agent/answer",
            headers=session_auth_headers("alice"),
            json={
                "session_id": sid,
                "request_id": "ask-1",
                "response": {"behavior": "allow"},
            },
        )
        assert answered.status_code == 200, answered.text
        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert any("alice 同意了" in (b.get("content") or "") for b in blocks), [
            b.get("content") for b in blocks
        ]
    finally:

        async def cleanup():
            await store().redis.delete(
                key(sid), key(topic, f"current:{agent}"), key(sid, "voiced")
            )

        client.portal.call(cleanup)


def test_the_question_it_asked_is_not_an_input_it_has_to_read(client, place):
    """芝士问出口的那句话落进房间，却不是一条交给它去读的输入。

    署名是房间里芝士那条 handle，轮次号这边填不出来 —— 问话的那一轮跑在机器
    上。按「署名是 agent 且落在某一轮里」去算，这条就成了待读输入：「忘了 @」的
    补救按钮于是不再答「没有待读的东西」，白开一轮，而那一轮的 prompt 里躺着芝
    士刚问出口的那句话，它对着自己的问题再答一遍。
    """
    from app.api.routes.remote_control import store

    project, topic, agent = place

    async def start():
        session = await store().create(
            {"p": project, "t": topic, "a": agent, "exp": int(time.time()) + 3600}, {}
        )
        return session["id"], await store().bridge(session)

    sid, worker = client.portal.call(start)
    try:
        landed = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers={"Authorization": f"Bearer {worker['worker_jwt']}"},
            json=_events(worker, "event-1"),
        )
        assert landed.status_code == 200, landed.text
        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert [b for b in blocks if "Bash" in (b.get("content") or "")], (
            "问出口的那句话得在房间里留下记录"
        )

        summoned = client.post(f"/topics/{topic}/summon", json={"author": "alice"})
        assert summoned.status_code == 200, summoned.text
        assert summoned.json()["data"] == {
            "started": False,
            "reason": "nothing_pending",
        }, "它自己问出口的那句话不该把它自己叫起来"
    finally:

        async def cleanup():
            await store().redis.delete(
                key(sid), key(topic, f"current:{agent}"), key(sid, "voiced")
            )

        client.portal.call(cleanup)


def test_a_session_that_never_recorded_its_agent_still_reports(client, place):
    """#1185 之前开的那批会话，Redis 里那一行没有 ``agent_handle``，而保留期是七天，
    所以它们还在。没有名字就没法把那句话署出去——从房间派生一个来签字，正是这次退役
    掉的那个答案。

    但这一档只让这句话不写进房间，不让它决定这条上报接口的返回码：事件在这一步之前
    就已经落库了，在这里失败会把 ``announce`` 一起跳过，于是面板——这些会话仅剩的那
    个 surface——再也不刷新，而 worker 每一批都撞进同一个失败，pending 又只有被人按
    下按钮才清得掉。
    """
    from app.api.routes.remote_control import store
    from app.domain.agent.runtime import get_broker

    project, topic, _ = place

    async def start():
        session = await store().create(
            {"p": project, "t": topic, "exp": int(time.time()) + 3600}, {}
        )
        return session["id"], await store().bridge(session)

    sid, worker = client.portal.call(start)
    subscription = get_broker().subscribe(str(topic), replay=False)
    queue = client.portal.call(subscription.__aenter__)
    try:
        reported = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers={"Authorization": f"Bearer {worker['worker_jwt']}"},
            json=_events(worker, "event-1"),
        )
        assert reported.status_code == 200, reported.text

        async def drain():
            frames = []
            while not queue.empty():
                frames.append(queue.get_nowait())
            return frames

        frames = client.portal.call(drain)
        control = [f for f in frames if f.get("type") == "agent_control"]
        assert control, f"面板没刷新：{[f.get('type') for f in frames]}"
        assert "ask-1" in control[-1]["state"]["pending"]

        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert not [b for b in blocks if "Bash" in (b.get("content") or "")], (
            "答不出是谁问的，就不该替它署一个名字"
        )
    finally:

        async def cleanup():
            await subscription.__aexit__(None, None, None)
            await store().redis.delete(
                key(sid), key(topic, "current"), key(sid, "voiced")
            )

        client.portal.call(cleanup)
