"""进度层 (#187): 芝士's checklist outlives the turn, the machine and the session.

The agent writes it with the platform tool `todo_write`, which lands on
``PUT /topics/{id}/progress``. These tests pin what makes the layer real: the
write survives the turn and the next turn is actually told about it — and, in
the room, the list is the agent's own message: posted by the first write, then
edited by the next ones, until the agent starts a new one.

A person writes a checklist through the same route, under the same message
rules, but their list is only their message: it never becomes the plan the
agent's next turn is handed back.
"""

import pytest

from app.api.deps import get_broker
from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    chat_ws_url,
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

PLAN = [
    {"content": "核实 issue 论断", "status": "completed"},
    {"content": "写实现", "status": "in_progress"},
    {"content": "补测试", "status": "pending"},
]


def _room(client) -> tuple[str, dict]:
    """A room, and the credentials its agent's session writes with."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]
    token = mint_scoped_token(project_id=p["id"], topic_id=t["id"])
    return t["id"], {"X-Cheese-Token": token}


def _write(client, topic_id: str, headers: dict, todos: list[dict], **extra):
    return client.put(
        f"/topics/{topic_id}/progress",
        json={"todos": todos, **extra},
        headers=headers,
    )


def _progress(client, topic_id: str, **params) -> list[tuple[str, str]]:
    data = client.get(f"/topics/{topic_id}/progress", params=params).json()["data"]
    return [(i["subject"], i["status"]) for i in data["items"]]


def _task(client, room_id: str) -> tuple[str, dict]:
    """One of the room's tasks, and the credentials its own session writes
    with."""
    task = open_task(client, room_id, owner="user-1", start=False)
    token = mint_scoped_token(
        project_id=task["project_id"], topic_id=room_id, task_id=task["id"]
    )
    return task["id"], {"X-Cheese-Token": token}


def _chat(client, topic_id: str) -> list[dict]:
    """Run one turn, returning every frame it produced."""
    frames: list[dict] = []
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        post_message(client, topic_id, "user-1", {"content": "@芝士 hi"})
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] in ("done", "error"):
                return frames


def test_topic_without_a_checklist_has_empty_progress(client):
    topic, _ = _room(client)
    body = client.get(f"/topics/{topic}/progress").json()
    assert body["code"] == 200
    assert body["data"] == {"items": [], "updated_at": None}


def _frame(ws, kind: str) -> dict:
    frame = ws.receive_json()
    while frame["type"] != kind:
        frame = ws.receive_json()
    return frame["block"]


def _checklists(client, topic_id: str) -> list[dict]:
    """The agent's messages in the room — in these tests, only its checklists."""
    agent = room_agent_seat(client, topic_id)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b for b in blocks if b["kind"] == "message" and b["author"] == agent]


CHECKLIST = "✓ 核实 issue 论断\n✱ 写实现\n○ 补测试"


def test_the_first_write_posts_the_checklist_as_the_agents_message(client):
    topic, headers = _room(client)
    with client.websocket_connect(chat_ws_url(topic, "user-1")) as ws:
        response = _write(client, topic, headers, PLAN)
        assert response.status_code == 200, response.text
        message = _frame(ws, "assistant_block")
    assert message["author"] == room_agent_seat(client, topic)
    assert message["kind"] == "message"
    assert message["content"] == CHECKLIST
    # The room draws the list from its structure, not from the text.
    checklist = message["meta"]["checklist"]
    assert [(i["subject"], i["status"]) for i in checklist["items"]] == [
        (t["content"], t["status"]) for t in PLAN
    ]
    assert checklist["result"] is None
    assert response.json()["data"]["message_id"] == message["id"]
    # 总览 reads the stored list, as before.
    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]
    data = client.get(f"/topics/{topic}/progress").json()["data"]
    # Stamped, so a reader can tell fresh progress from something ancient.
    assert data["updated_at"] is not None


def test_later_writes_edit_that_same_message(client):
    topic, headers = _room(client)
    first = _write(client, topic, headers, PLAN).json()["data"]["message_id"]
    done = [{**t, "status": "completed"} for t in PLAN]
    with client.websocket_connect(chat_ws_url(topic, "user-1")) as ws:
        response = _write(client, topic, headers, done)
        assert response.status_code == 200, response.text
        edited = _frame(ws, "block_updated")
    assert edited["id"] == first
    assert edited["content"] == "✓ 核实 issue 论断\n✓ 写实现\n✓ 补测试"
    assert edited["meta"]["edited_at"], "an update is an edit, and shows as one"
    assert [m["id"] for m in _checklists(client, topic)] == [first]


def test_the_agent_starts_a_new_checklist_when_it_says_so(client):
    topic, headers = _room(client)
    first = _write(client, topic, headers, PLAN).json()["data"]["message_id"]
    second = _write(
        client,
        topic,
        headers,
        [{"content": "新请求第一步", "status": "in_progress"}],
        new=True,
    ).json()["data"]["message_id"]
    assert second != first
    third = _write(
        client, topic, headers, [{"content": "新请求第一步", "status": "completed"}]
    ).json()["data"]["message_id"]
    assert third == second, "without `new`, the newest checklist is the one edited"
    shown = {m["id"]: m["content"] for m in _checklists(client, topic)}
    assert shown[first] == CHECKLIST, "the earlier checklist stays as it was"
    assert set(shown) == {first, second}


def test_the_last_write_puts_the_result_under_the_list(client):
    topic, headers = _room(client)
    first = _write(client, topic, headers, PLAN).json()["data"]["message_id"]
    done = [{**t, "status": "completed"} for t in PLAN]
    with client.websocket_connect(chat_ws_url(topic, "user-1")) as ws:
        _write(client, topic, headers, done, result="接口改好了，测试全过")
        edited = _frame(ws, "block_updated")
    assert edited["id"] == first
    assert edited["content"].endswith("✓ 补测试\n\n✅ 接口改好了，测试全过")
    assert edited["meta"]["checklist"]["result"] == "接口改好了，测试全过"


def test_text_written_over_a_checklist_is_no_longer_one(client):
    """The agent can still edit its checklist message by hand (`chat_edit`).
    What it writes is its own words, shown as written, and the next
    `todo_write` starts a fresh list rather than overwrite them."""
    topic, headers = _room(client)
    first = _write(client, topic, headers, PLAN).json()["data"]["message_id"]
    response = client.patch(
        f"/blocks/{first}", json={"content": "计划改了，稍后重列"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert "checklist" not in response.json()["data"]["meta"]
    again = _write(client, topic, headers, PLAN).json()["data"]
    assert again["posted"] is True and again["message_id"] != first


def _progress_section(told: str) -> str:
    """The checklist the session is handed back as its own, from its opening."""
    start = told.index("上次的任务清单")
    end = told.find("清单会整份覆盖", start)
    return told[start:end]


def _new_conversation(client, topic: str) -> None:
    """The room's conversation is gone: the next turn opens a new one."""
    import uuid

    from app.domain.agent_session.services import AgentSessionService

    async def forget() -> None:
        async with client.test_factory() as session:
            await AgentSessionService(session).forget_room(uuid.UUID(topic))
            await session.commit()

    client.portal.call(forget)


def test_each_write_replaces_the_whole_list(client):
    topic, headers = _room(client)
    _write(client, topic, headers, PLAN)
    _write(client, topic, headers, [{"content": "只剩这一项", "status": "pending"}])
    assert _progress(client, topic) == [("只剩这一项", "pending")]


def test_a_new_session_is_told_where_the_work_got_to(client, stub_hooks):
    """The list is for the session that was not there: one that goes on holds
    its own history, a new one (a deploy, a recycled machine) is handed it."""
    topic, headers = _room(client)
    _chat(client, topic)
    assert "上次的任务清单" not in stub_hooks.told

    _write(client, topic, headers, PLAN)
    _new_conversation(client, topic)
    _chat(client, topic)

    prompt = stub_hooks.told
    # Status is carried as a mark, not just the text: "已完成" vs "在做" is the
    # whole reason to hand the list over rather than re-plan from scratch.
    assert "- [x] 核实 issue 论断" in prompt
    assert "- [~] 写实现" in prompt
    assert "- [ ] 补测试" in prompt


def test_a_tasks_checklist_stays_on_its_task(client, stub_hooks, monkeypatch):
    """A task's session writes its list into the room's progress route, naming
    its task. That plan is the task's: the room's stored list, and what the
    room's next turn is handed back, stay the room's own."""
    topic, headers = _room(client)
    task, task_headers = _task(client, topic)
    _write(client, topic, headers, PLAN)
    broker = get_broker()
    published: list[tuple[str, dict]] = []
    publish = broker.publish

    async def spy(channel, frame):
        published.append((channel, frame))
        await publish(channel, frame)

    monkeypatch.setattr(broker, "publish", spy)
    plan = [{"content": "改任务里的接口", "status": "in_progress"}]
    response = _write(client, topic, task_headers, plan, task=task)
    assert response.status_code == 200, response.text

    assert _progress(client, topic, task=task) == [("改任务里的接口", "in_progress")]
    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]
    assert [(c, f["type"]) for c, f in published] == [(task, "todo")], (
        "the task's list goes to the task, and posts nothing in the room"
    )
    assert len(_checklists(client, topic)) == 1, "only the room's own checklist"

    monkeypatch.setattr(broker, "publish", publish)
    _chat(client, topic)
    prompt = stub_hooks.told
    assert "- [~] 写实现" in prompt
    assert "改任务里的接口" not in prompt


def test_a_task_from_another_room_is_refused(client):
    topic, headers = _room(client)
    other, _ = _room(client)
    foreign, _ = _task(client, other)
    assert _write(client, topic, headers, PLAN, task=foreign).status_code == 404
    assert _progress(client, topic) == []
    assert _progress(client, other, task=foreign) == []


@pytest.mark.parametrize(
    "todos",
    [
        [],
        [{"content": "x", "status": "done"}],
        [{"content": "   ", "status": "pending"}],
        [{"content": "长" * 201, "status": "pending"}],
        [{"content": f"第 {n} 步", "status": "pending"} for n in range(31)],
    ],
    ids=["empty", "unknown-status", "blank", "too-long", "too-many"],
)
def test_a_malformed_checklist_changes_nothing(client, todos):
    topic, headers = _room(client)
    _write(client, topic, headers, PLAN)
    assert _write(client, topic, headers, todos).status_code == 400
    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]


def test_a_caller_the_room_does_not_admit_writes_nothing(client):
    topic, headers = _room(client)
    other, _ = _room(client)
    anonymous = {"X-Cheese-Token": ""}
    assert _write(client, topic, anonymous, PLAN).status_code == 401
    # The development token opens rooms but is nobody, and a list needs a writer.
    assert _write(client, topic, {}, PLAN).status_code == 403
    assert _write(client, other, headers, PLAN).status_code == 403
    assert _progress(client, topic) == []
    assert _progress(client, other) == []


def _shared_room(client) -> str:
    """A room of alice's project that bob is also in."""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    return client.post(
        "/topics",
        json={"project_id": project["id"], "title": "话题"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _messages_by(client, topic_id: str, author: str) -> list[dict]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b for b in blocks if b["kind"] == "message" and b["author"] == author]


def test_a_person_posts_a_checklist_as_their_own_message(client):
    room = _shared_room(client)
    alice = session_auth_headers("alice")
    with client.websocket_connect(chat_ws_url(room, "bob")) as bob:
        response = _write(client, room, alice, PLAN)
        assert response.status_code == 200, response.text
        seen = _frame(bob, "assistant_block")
    assert seen["author"] == "alice"
    assert seen["content"] == CHECKLIST
    assert [
        (i["subject"], i["status"]) for i in seen["meta"]["checklist"]["items"]
    ] == [(t["content"], t["status"]) for t in PLAN]
    assert response.json()["data"]["message_id"] == seen["id"]


def test_a_persons_next_write_edits_their_checklist_and_new_starts_another(client):
    room = _shared_room(client)
    alice = session_auth_headers("alice")
    first = _write(client, room, alice, PLAN).json()["data"]["message_id"]
    done = [{**t, "status": "completed"} for t in PLAN]
    with client.websocket_connect(chat_ws_url(room, "bob")) as bob:
        assert _write(client, room, alice, done).status_code == 200
        edited = _frame(bob, "block_updated")
    assert edited["id"] == first
    assert edited["content"] == "✓ 核实 issue 论断\n✓ 写实现\n✓ 补测试"
    assert edited["meta"]["edited_at"]

    second = _write(client, room, alice, PLAN, new=True).json()["data"]
    assert second["posted"] is True
    assert [m["id"] for m in _messages_by(client, room, "alice")] == [
        first,
        second["message_id"],
    ]


def test_nobody_writes_someone_elses_checklist(client):
    """bob writing a list makes a list of his; alice's stays as she left it."""
    room = _shared_room(client)
    alice_list = _write(client, room, session_auth_headers("alice"), PLAN).json()[
        "data"
    ]["message_id"]
    mine = [{"content": "我的事", "status": "pending"}]
    bob_list = _write(client, room, session_auth_headers("bob"), mine).json()["data"]
    assert bob_list["posted"] is True
    assert bob_list["message_id"] != alice_list
    (alice_message,) = _messages_by(client, room, "alice")
    assert alice_message["content"] == CHECKLIST
    assert "edited_at" not in alice_message["meta"]
    # Nor through the edit every message has.
    assert (
        client.patch(
            f"/blocks/{alice_list}",
            json={"content": "改掉"},
            headers=session_auth_headers("bob"),
        ).status_code
        == 403
    )


def test_a_person_ticks_a_step_on_an_earlier_checklist_of_theirs(client):
    """The list a person is looking at is the one that changes, not just their
    newest."""
    room = _shared_room(client)
    alice = session_auth_headers("alice")
    older = _write(client, room, alice, PLAN).json()["data"]["message_id"]
    newer = _write(client, room, alice, PLAN, new=True).json()["data"]["message_id"]
    ticked = [{**PLAN[0]}, {**PLAN[1], "status": "completed"}, {**PLAN[2]}]
    response = _write(client, room, alice, ticked, message=older)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["message_id"] == older
    shown = {m["id"]: m["content"] for m in _messages_by(client, room, "alice")}
    assert shown[older] == "✓ 核实 issue 论断\n✓ 写实现\n○ 补测试"
    assert shown[newer] == CHECKLIST


def test_naming_someone_elses_checklist_changes_nothing(client):
    room = _shared_room(client)
    alice_list = _write(client, room, session_auth_headers("alice"), PLAN).json()[
        "data"
    ]["message_id"]
    done = [{**t, "status": "completed"} for t in PLAN]
    bob = session_auth_headers("bob")
    assert _write(client, room, bob, done, message=alice_list).status_code == 403
    (alice_message,) = _messages_by(client, room, "alice")
    assert alice_message["content"] == CHECKLIST
    assert _messages_by(client, room, "bob") == []


def test_only_a_checklist_is_written_as_one(client):
    """A plain message is not turned into a list by naming it."""
    room = _shared_room(client)
    said = post_message(client, room, "alice", {"content": "周五交初稿"})
    alice = session_auth_headers("alice")
    assert _write(client, room, alice, PLAN, message=said["id"]).status_code == 404
    (message,) = _messages_by(client, room, "alice")
    assert message["content"] == "周五交初稿"


def test_a_person_outside_the_room_cannot_post_one(client):
    room = _shared_room(client)
    assert (
        _write(client, room, session_auth_headers("mallory"), PLAN).status_code == 403
    )
    assert _messages_by(client, room, "mallory") == []


def test_a_persons_checklist_is_not_the_agents_plan(client, stub_hooks):
    """The room's stored list is what its next turn is handed back as its own
    progress; a person's list must not become that."""
    topic, headers = _room(client)
    _write(client, topic, headers, PLAN)
    person = [{"content": "我自己的待办", "status": "in_progress"}]
    assert (
        _write(client, topic, session_auth_headers("user-1"), person).status_code == 200
    )

    assert _progress(client, topic) == [(t["content"], t["status"]) for t in PLAN]
    _chat(client, topic)
    # The person's list still reaches the agent as what they said; it must not
    # be handed back as the agent's own progress.
    progress = _progress_section(stub_hooks.told)
    assert "- [~] 写实现" in progress
    assert "我自己的待办" not in progress
    # And the agent's own message is left alone.
    (agent_list,) = _checklists(client, topic)
    assert agent_list["content"] == CHECKLIST


def test_a_person_does_not_write_a_tasks_checklist(client):
    topic, _ = _room(client)
    task, _ = _task(client, topic)
    person = [{"content": "x", "status": "pending"}]
    response = _write(client, topic, session_auth_headers("user-1"), person, task=task)
    assert response.status_code == 403
    assert _progress(client, topic, task=task) == []


def test_progress_is_per_topic(client):
    a, headers = _room(client)
    b, _ = _room(client)
    _write(client, a, headers, PLAN)
    assert _progress(client, b) == []


def test_progress_404s_for_an_unknown_topic(client):
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/topics/{unknown}/progress").status_code == 404
