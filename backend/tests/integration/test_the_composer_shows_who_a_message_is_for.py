"""Before a message is sent, the sender can see who it is for; after it is sent
into a channel where nobody else is, they are told nobody was asked.

The rules a person can state without the code:

- A draft is for the AI teammate exactly when sending it would start the
  teammate's turn: it @-s the teammate, it replies to the teammate's message,
  or its task's owner says it in an open task. Otherwise it is for the people
  it @-s, or for nobody in particular.
- Asking who a draft is for writes nothing and wakes nobody.
- A message sent as one for the teammate that no longer is (its task closed
  while it was typed) is not posted as discussion; the sender is told whom it
  is for now.
- A message that asks nobody, said where its sender is the only person, comes
  back saying so. 综合 holds everyone in the project whether or not they ever
  opened it.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.api.deps import get_work_runner
from app.domain.agent.models import AgentTurn
from app.domain.agent.turn.intake import human
from app.domain.room_task.closing import close_after_summary
from app.domain.room_task.models import Task, TaskStatus
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    mint_scoped_token,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str, str]:
    """A project of alice's, its 综合, and one more channel of hers."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], project["root_topic_id"], room


def _for_whom(client, conversation: str, who: str = "alice", **draft) -> dict:
    r = client.post(
        f"/topics/{conversation}/messages/addressing",
        json=draft,
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _task_with_a_turn(client, room: str) -> tuple[str, str]:
    """A 支线 the teammate answered once in, turned into alice's task. Returns
    the task and the teammate's seat."""
    seat = room_agent_seat(client, room)
    root = post_message(client, room, "alice", {"content": "登录要不要加验证码？"})
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    with room_socket(client, thread, "alice") as ws:
        post_message(client, thread, "alice", {"content": f"<@{seat}> 先看看"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    made = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text
    return thread, seat


def _written(client, conversation: str) -> tuple[int, int]:
    """How many lines the conversation holds, and how many turns it has had."""
    lines = client.get(
        f"/topics/{conversation}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]

    async def turns() -> int:
        async with client.test_factory() as db:
            return await db.scalar(
                select(func.count())
                .select_from(AgentTurn)
                .where(AgentTurn.conversation_id == uuid.UUID(conversation))
            )

    return len(lines), client.portal.call(turns)


def test_a_plain_message_in_a_channel_is_for_nobody(client, stub_hooks):
    _, _, room = _channel(client)

    drafted = _for_whom(client, room, content="明天几点开会？")

    assert drafted["agent"] is None
    assert drafted["place"] == "channel"


def test_adding_and_removing_the_at_changes_who_it_is_for(client, stub_hooks):
    _, _, room = _channel(client)
    seat = room_agent_seat(client, room)

    named = _for_whom(client, room, content=f"<@{seat}> 整理一下需求")
    unnamed = _for_whom(client, room, content="整理一下需求")

    assert named["agent"]["handle"] == seat
    assert named["agent"]["name"]
    assert named["reason"] == "mention"
    assert unnamed["agent"] is None


def test_a_reply_to_the_teammate_is_for_the_teammate(client, stub_hooks):
    project, _, room = _channel(client)
    thread = in_thread(client, room, "alice")
    # The teammate says something in the 支线 with its own turn's credential.
    token = mint_scoped_token(project_id=project, topic_id=thread)
    said = client.post(
        f"/topics/{thread}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "要我先看哪一块？"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert said.status_code == 200, said.text
    answer = said.json()["data"]

    replying = _for_whom(client, thread, content="登录", reply_to=answer["id"])
    not_replying = _for_whom(client, thread, content="登录")

    assert replying["agent"]["handle"] == answer["author"]
    assert replying["reason"] == "reply"
    assert not_replying["agent"] is None


def test_the_owner_in_an_open_task_speaks_to_its_teammate(client, stub_hooks):
    project, _, room = _channel(client)
    join_project_team(client, project, "bob")
    task, seat = _task_with_a_turn(client, room)

    plain = _for_whom(client, task, content="先做后端两个接口")
    to_bob = _for_whom(client, task, content="<@bob> 你看下这个接口")

    assert plain["agent"]["handle"] == seat
    assert plain["reason"] == "task"
    assert plain["task_status"] == "open"
    # An @ of a person alone is said to that person.
    assert to_bob["agent"] is None
    assert to_bob["people"] == ["bob"]


def test_a_closed_task_hears_only_whoever_names_its_teammate(client, stub_hooks):
    _, _, room = _channel(client)
    task, seat = _task_with_a_turn(client, room)
    client.post(f"/topics/{task}/close", json={}, headers=session_auth_headers("alice"))

    plain = _for_whom(client, task, content="验证码几分钟过期？")
    named = _for_whom(client, task, content=f"<@{seat}> 验证码几分钟过期？")

    assert plain["agent"] is None
    assert plain["task_status"] == "closed"
    assert named["agent"]["handle"] == seat


def test_asking_who_a_draft_is_for_writes_nothing_and_wakes_nobody(client, stub_hooks):
    _, _, room = _channel(client)
    seat = room_agent_seat(client, room)
    before = _written(client, room)
    prompt_before = stub_hooks.last_prompt

    drafted = _for_whom(client, room, content=f"<@{seat}> 整理一下需求")

    assert drafted["agent"]["handle"] == seat
    assert _written(client, room) == before
    assert stub_hooks.last_prompt == prompt_before


def test_a_send_meant_for_the_teammate_of_a_task_closed_meanwhile_is_refused(
    client, stub_hooks
):
    _, _, room = _channel(client)
    task, _ = _task_with_a_turn(client, room)
    # Shown 「交给」 the teammate while the task was open...
    assert _for_whom(client, task, content="再加一个接口")["reason"] == "task"
    client.post(f"/topics/{task}/close", json={}, headers=session_auth_headers("alice"))
    before = _written(client, task)

    r = client.post(
        f"/topics/{task}/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "content": "再加一个接口",
            "expects_agent": True,
        },
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["name"] == "AddressingChanged"
    assert error["data"]["agent"] is None
    assert error["data"]["task_status"] == "closed"
    assert _written(client, task) == before


def test_a_task_closed_while_the_send_is_on_its_way_refuses_it(
    client, stub_hooks, monkeypatch
):
    """The close lands after the request read the task and before the message
    is written: what decides is the task as it is when the message is written."""
    _, _, room = _channel(client)
    task, _ = _task_with_a_turn(client, room)
    lines_before, _ = _written(client, task)
    runner = get_work_runner()
    receive = runner.receive_message

    async def closed_meanwhile(*args, **kwargs):
        async with client.test_factory() as db:
            await db.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task))
                .values(status=TaskStatus.closed)
            )
            await db.commit()
        return await receive(*args, **kwargs)

    monkeypatch.setattr(runner, "receive_message", closed_meanwhile)
    r = client.post(
        f"/topics/{task}/messages",
        json={
            "request_id": str(uuid.uuid4()),
            "content": "再加一个接口",
            "expects_agent": True,
        },
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 409, r.text
    assert r.json()["error"]["data"]["task_status"] == "closed"
    assert r.json()["error"]["data"]["agent"] is None
    # Lines only: the task's first turn may still be settling in the background.
    assert _written(client, task)[0] == lines_before


def test_a_task_waiting_for_its_write_up_is_not_closed_over_a_message_being_sent(
    client, stub_hooks, monkeypatch
):
    """A task whose write-up was given up on closes only while nothing else is
    asked in it. A message being sent in it as that close runs is either seen
    waiting, and the task stays open, or lands after the close; it is never
    stored for the teammate and then closed over."""
    _, _, room = _channel(client)
    task, _ = _task_with_a_turn(client, room)
    runner = get_work_runner()
    # The message stays waiting to be read: its turn is not started here.
    monkeypatch.setattr(runner._intake, "_schedule_message", lambda *a, **k: None)

    async def waiting_for_its_write_up():
        async with client.test_factory() as db:
            await db.execute(
                update(Task)
                .where(Task.id == uuid.UUID(task))
                .values(closing_since=datetime.now(UTC))
            )
            await db.commit()

    client.portal.call(waiting_for_its_write_up)
    prepare = human.prepare_human

    async def race():
        reading = asyncio.Event()
        closing = asyncio.Event()

        async def send_waits_for_the_close(session, place, *args, **kwargs):
            # The message has read its task; the close starts and goes as far
            # as it can before the message is written.
            if place.conversation_id == uuid.UUID(task):
                reading.set()
                await closing.wait()
                await asyncio.sleep(0.5)
            return await prepare(session, place, *args, **kwargs)

        async def give_up_on_the_write_up():
            async with client.test_factory() as db:
                closing.set()
                closed = await close_after_summary(db, uuid.UUID(task), None)
                await db.commit()
                return closed

        monkeypatch.setattr(human, "prepare_human", send_waits_for_the_close)
        send = asyncio.create_task(
            asyncio.to_thread(
                client.post,
                f"/topics/{task}/messages",
                json={
                    "request_id": str(uuid.uuid4()),
                    "content": "收尾前再改一处",
                    "expects_agent": True,
                },
                headers=session_auth_headers("alice"),
            )
        )
        await asyncio.wait_for(reading.wait(), 5)
        close = asyncio.create_task(give_up_on_the_write_up())
        return await asyncio.wait_for(
            asyncio.gather(send, close, return_exceptions=True), 15
        )

    sent, closed = client.portal.call(race)

    assert not isinstance(sent, BaseException), repr(sent)
    assert not isinstance(closed, BaseException), repr(closed)
    if sent.status_code == 200:
        assert sent.json()["data"]["meta"]["agent_recipient"]["mentioned"] is True
        assert closed is None
    else:
        assert sent.status_code == 409, sent.text


def test_a_send_still_for_the_teammate_goes_through(client, stub_hooks):
    _, _, room = _channel(client)
    task, _ = _task_with_a_turn(client, room)

    sent = post_message(
        client, task, "alice", {"content": "再加一个接口", "expects_agent": True}
    )

    assert sent["meta"]["agent_recipient"]["mentioned"] is True


def test_asking_nobody_where_you_are_alone_says_so(client, stub_hooks):
    _, general, room = _channel(client)
    seat = room_agent_seat(client, room)

    in_general = post_message(client, general, "alice", {"content": "帮我列个计划"})
    in_room = post_message(client, room, "alice", {"content": "帮我列个计划"})
    asked = post_message(client, room, "alice", {"content": f"<@{seat}> 帮我列个计划"})

    assert in_general.get("alone") is True
    assert in_room.get("alone") is True
    assert "alone" not in asked


def test_nobody_is_told_they_are_alone_where_someone_else_is(client, stub_hooks):
    project, general, _ = _channel(client)
    # bob is in the project and so in 综合, though he never opened it.
    join_project_team(client, project, "bob")

    said = post_message(client, general, "alice", {"content": "明天几点开会？"})

    assert "alone" not in said
