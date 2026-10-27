"""A 支线 that has become one piece of work turns into a task where it is.

The rules a person can state without the code: turning a 支线 into a task keeps
the conversation — the same id, everything said in it, the AI teammate's
session — and from then on that session's work is kept. Turning it twice
makes one task. The message it hangs under shows the same conversation, now
with an owner. Anyone in the project speaks in it, and what its AI teammate
acts on unasked is its owner's word; once it is closed, it is talked in as a
支线 is.
"""

import uuid

from sqlalchemy import select, text

from app.domain.agent_session.models import AgentSession
from app.domain.room_task.place import PlaceResolver
from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
    post_message,
    post_project,
    read_task,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str]:
    alice = session_auth_headers("alice")
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "开发"}, headers=alice
    ).json()["data"]["id"]
    return project["id"], room


def _thread_with_a_turn(client, room: str) -> tuple[dict, str, str]:
    """A message in the main line, and its 支线 with the teammate answering
    once in it."""
    seat = room_agent_seat(client, room)
    root = post_message(client, room, "alice", {"content": "登录要不要加手机验证码？"})
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    with room_socket(client, thread, "alice") as ws:
        post_message(
            client, thread, "alice", {"content": f"<@{seat}> 先看看现在怎么登录"}
        )
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    return root, thread, seat


def _messages(client, conversation: str) -> list[dict]:
    r = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return [b for b in r.json()["data"]["data"] if b["kind"] == "message"]


def test_turning_a_thread_keeps_its_conversation_and_session(client, stub_hooks):
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    said_before = [b["id"] for b in _messages(client, thread)]
    assert said_before

    made = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text
    task = made.json()["data"]

    assert task["id"] == thread
    assert read_task(client, thread)["owner_handle"] == "alice"
    said_after = [b["id"] for b in _messages(client, thread)]
    assert said_after[: len(said_before)] == said_before

    async def read():
        async with client.test_factory() as db:
            sessions = (
                await db.scalars(
                    select(AgentSession).where(
                        AgentSession.conversation_id == uuid.UUID(thread)
                    )
                )
            ).all()
            place = await PlaceResolver(db).conversation(uuid.UUID(thread))
            kind = await db.scalar(
                text("SELECT kind FROM conversations WHERE id = :id"), {"id": thread}
            )
            return sessions, place, kind

    sessions, place, kind = client.portal.call(read)
    assert sessions, "the teammate's session in the 支线 is gone"
    assert kind == "task"
    assert place is not None and place.task is not None
    assert place.keeps_work


def test_the_line_says_who_owns_it(client, stub_hooks):
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    client.post(f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice"))

    lines = client.get(
        f"/topics/{thread}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    became = [
        b
        for b in lines
        if (b.get("meta") or {}).get("i18n", {}).get("content", {}).get("key")
        == "threadBecameTask"
    ]
    assert len(became) == 1
    assert "alice" in became[0]["content"]


def test_turning_it_twice_makes_one_task(client, stub_hooks):
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    alice = session_auth_headers("alice")

    first = client.post(f"/blocks/{root['id']}/upgrade", headers=alice)
    second = client.post(f"/blocks/{root['id']}/upgrade", headers=alice)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"] == thread
    tasks = client.get(f"/topics/{room}/tasks", headers=alice).json()["data"]["data"]
    assert [t["id"] for t in tasks] == [thread]


def test_a_message_nobody_replied_to_becomes_a_task_with_its_own_conversation(
    client, stub_hooks
):
    _, room = _channel(client)
    root = post_message(client, room, "alice", {"content": "首页卡片改成两列"})
    made = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text
    task = made.json()["data"]
    thread = client.get(
        f"/topics/{task['id']}/thread", headers=session_auth_headers("alice")
    )
    assert thread.status_code == 200, thread.text
    assert thread.json()["data"]["root_block_id"] == root["id"]
    assert thread.json()["data"]["task"] == task["id"]


def test_the_message_shows_the_same_conversation_as_a_task(client, stub_hooks):
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    alice = session_auth_headers("alice")

    listed = client.get(f"/topics/{room}/threads", headers=alice).json()["data"]["open"]
    assert [t["id"] for t in listed] == [thread]

    client.post(f"/blocks/{root['id']}/upgrade", headers=alice)

    assert (
        client.get(f"/topics/{room}/threads", headers=alice).json()["data"]["open"]
        == []
    )
    main_line = client.get(f"/topics/{room}/blocks", headers=alice).json()["data"][
        "data"
    ]
    under = next(b for b in main_line if b["id"] == root["id"])
    assert under["thread"]["id"] == thread
    assert [t["id"] for t in under["tasks"]] == [thread]


def test_someone_else_speaks_in_the_task_without_calling_its_teammate(
    client, stub_hooks
):
    project, room = _channel(client)
    join_project_team(client, project, "bob")
    root, thread, _ = _thread_with_a_turn(client, room)
    client.post(f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice"))

    said = post_message(client, thread, "bob", {"content": "顺便看看注册页"})

    assert said["meta"]["agent_recipient"]["mentioned"] is False


def test_the_owner_calls_its_teammate_without_an_at(client, stub_hooks):
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    client.post(f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice"))

    said = post_message(client, thread, "alice", {"content": "先做后端两个接口"})

    assert said["meta"]["agent_recipient"]["mentioned"] is True


def test_its_teammate_turns_the_thread_on_someones_word(client, stub_hooks):
    project, room = _channel(client)
    join_project_team(client, project, "bob")
    root, thread, seat = _thread_with_a_turn(client, room)
    post_message(client, thread, "bob", {"content": f"<@{seat}> 转成任务吧，我来负责"})

    token = mint_scoped_token(project_id=project, topic_id=thread, agent_handle=seat)
    bearer = client.headers.pop("Authorization", None)
    try:
        made = client.post(
            f"/topics/{thread}/teammate-tasks",
            json={"title": "登录验证码", "here": True},
            headers={"X-Cheese-Token": token},
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer

    assert made.status_code == 200, made.text
    task = read_task(client, thread)
    assert task["id"] == thread
    assert task["owner_handle"] == "bob"
    assert task["title"] == "登录验证码"


def test_asking_to_open_here_inside_a_task_hands_that_task_back(client, stub_hooks):
    """已经在任务里，队友又说「就在这儿开」：不是「这儿不能开」，是「你要的
    地方就是这里」。回 200 而不是 422，并把这条任务交回去——2026-10-21，那句
    「只有支线可以原地转为任务」被读成「这里没有任务」，于是同一件事被另开成
    一条任务、两个 AI 各做了一份。"""
    project, room = _channel(client)
    root, thread, seat = _thread_with_a_turn(client, room)
    client.post(f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice"))

    token = mint_scoped_token(project_id=project, topic_id=thread, agent_handle=seat)
    bearer = client.headers.pop("Authorization", None)
    try:
        again = client.post(
            f"/topics/{thread}/teammate-tasks",
            json={"title": "再做一遍", "here": True},
            headers={"X-Cheese-Token": token},
        )
        listed = client.get(
            f"/topics/{room}/tasks", headers=session_auth_headers("alice")
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer

    assert again.status_code == 200, again.text
    body = again.json()["data"]
    assert body["id"] == thread
    assert body["already"] is True
    # 没有再开一条：房间里还是只有这一件。
    assert [t["id"] for t in listed.json()["data"]["data"]] == [thread]


def test_a_closed_task_is_still_talked_in_and_keeps_nothing(client, stub_hooks):
    """After a task closes, people can still ask about it there; its teammate
    answers when named, as in a 支线, and nothing it does reaches the project
    until the task is reopened."""
    _, room = _channel(client)
    root, thread, seat = _thread_with_a_turn(client, room)
    alice = session_auth_headers("alice")
    client.post(f"/blocks/{root['id']}/upgrade", headers=alice)
    closed = client.post(f"/topics/{thread}/close", json={}, headers=alice)
    assert closed.status_code == 200, closed.text

    plain = post_message(client, thread, "alice", {"content": "这个验证码几分钟过期？"})
    named = post_message(
        client, thread, "alice", {"content": f"<@{seat}> 这个验证码几分钟过期？"}
    )

    assert plain["meta"]["agent_recipient"]["mentioned"] is False
    assert named["meta"]["agent_recipient"]["mentioned"] is True

    async def keeps_work() -> bool:
        async with client.test_factory() as db:
            place = await PlaceResolver(db).conversation(uuid.UUID(thread))
            return place.keeps_work

    assert client.portal.call(keeps_work) is False


def test_a_task_made_from_a_reply_shows_under_the_reply_and_the_message(
    client, stub_hooks
):
    """A task split off from something said in a 支线 is found both where it
    was said and under the main-line message the 支线 hangs on, and opens
    beside the channel as a 支线 does, quoting what it was made from."""
    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)
    alice = session_auth_headers("alice")
    reply = post_message(client, thread, "alice", {"content": "短信接口单独做一件"})

    made = client.post(f"/blocks/{reply['id']}/upgrade", headers=alice)
    assert made.status_code == 200, made.text
    task = made.json()["data"]["id"]
    assert task != thread

    in_thread = next(b for b in _messages(client, thread) if b["id"] == reply["id"])
    assert [t["id"] for t in in_thread["tasks"]] == [task]
    main_line = _messages(client, room)
    under = next(b for b in main_line if b["id"] == root["id"])
    assert [t["id"] for t in under["tasks"]] == [task]

    beside = client.get(f"/topics/{task}/thread", headers=alice)
    assert beside.status_code == 200, beside.text
    assert beside.json()["data"]["task"] == task
    assert beside.json()["data"]["root"]["id"] == reply["id"]


def test_turning_it_starts_its_teammate_at_once(client, stub_hooks):
    """A person turning a 支线 does not leave it waiting for someone to speak:
    its AI teammate is handed the work at once (to put the discussion in the
    task's document first)."""
    from app.domain.delivery.models import Delivery

    _, room = _channel(client)
    root, thread, _ = _thread_with_a_turn(client, room)

    async def handed() -> int:
        async with client.test_factory() as db:
            return len(
                list(
                    await db.scalars(
                        select(Delivery).where(
                            Delivery.conversation_id == uuid.UUID(thread)
                        )
                    )
                )
            )

    before = client.portal.call(handed)
    client.post(f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice"))

    assert client.portal.call(handed) == before + 1
