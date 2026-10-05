"""A task: one person's piece of work, talked through with its AI teammate in a
conversation of its own, beside the room it came from.

The rules a person could state before any of this was built:

- a person creates a task, and owns it; an AI teammate may only propose one;
- only the owner talks in the task — everyone else says what they have to say
  in the room;
- nothing is changed in the project until the owner starts the task, and what
  the task's document said then is what its changes are reviewed against;
- the task's conversation and its AI session are its own: talking in it leaves
  the room's history and the room's session alone, and the reverse;
- a task's document is readable by whoever can see the task and written only
  by its owner and its own session;
- a closed task takes no more messages.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_message,
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return project["id"], room["id"]


def _with_bob(client, project_id: str, room_id: str) -> None:
    """Bob can see the room and everything in it, and owns nothing in it."""
    join_project_team(client, project_id, "bob")
    assert (
        client.get(
            f"/topics/{room_id}", headers=session_auth_headers("bob")
        ).status_code
        == 200
    )


def _task_session_headers(client, project_id, room_id, task_id) -> dict:
    """The credential a task's own AI session presents."""
    token = mint_scoped_token(
        project_id=project_id,
        topic_id=str(task_id),
        agent_handle=room_agent_seat(client, room_id),
    )
    return {"X-Cheese-Token": token}


def _say_in_task(client, room_id, task_id, headers, content="先看看现状"):
    return client.post(
        f"/topics/{task_id}/messages",
        json={"content": content, "request_id": str(uuid.uuid4())},
        headers=headers,
    )


def _task(client, room_id, task_id, handle="alice") -> dict:
    r = client.get(f"/topics/{task_id}/task", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _task_contents(client, task_id) -> list[str]:
    r = client.get(f"/topics/{task_id}/blocks", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return [b["content"] for b in r.json()["data"]["data"]]


def _room_contents(client, room_id) -> list[str]:
    r = client.get(f"/topics/{room_id}/blocks", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return [b["content"] for b in r.json()["data"]["data"]]


# —— 谁能创建 ————————————————————————————————————————————————————————————


def test_the_person_who_creates_a_task_owns_it_and_it_has_not_started(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)

    task = open_task(client, room_id, "整理接口", owner="bob", start=False)

    assert task["owner_handle"] == "bob"
    assert task["started_at"] is None


def test_an_ai_teammate_cannot_create_a_task(client):
    _project_id, room_id = _room(client)

    r = client.post(
        f"/topics/{room_id}/tasks",
        json={"title": "我自己开一个"},
        headers=room_agent_headers(client, room_id),
    )

    assert r.status_code == 403
    assert client.get(f"/topics/{room_id}/tasks").json()["data"]["data"] == []


def test_a_proposal_becomes_a_task_owned_by_whoever_accepts_it(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    proposal = client.post(
        f"/topics/{room_id}/task-proposals",
        json={"title": "迁移旧数据", "summary": "把旧表搬到新表"},
        headers=room_agent_headers(client, room_id),
    )
    assert proposal.status_code == 200, proposal.text
    block_id = proposal.json()["data"]["id"]

    accepted = client.post(
        f"/topics/{room_id}/task-proposals/{block_id}/accept",
        headers=session_auth_headers("bob"),
    )

    assert accepted.status_code == 200, accepted.text
    task = accepted.json()["data"]
    assert task["owner_handle"] == "bob"
    assert task["title"] == "迁移旧数据"
    # One proposal, one task: the second person to click gets told, not a twin.
    again = client.post(
        f"/topics/{room_id}/task-proposals/{block_id}/accept",
        headers=session_auth_headers("alice"),
    )
    assert again.status_code == 422
    assert len(client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]) == 1


def test_a_dismissed_proposal_cannot_be_accepted_later(client):
    _project_id, room_id = _room(client)
    block_id = client.post(
        f"/topics/{room_id}/task-proposals",
        json={"title": "顺手重构"},
        headers=room_agent_headers(client, room_id),
    ).json()["data"]["id"]

    dismissed = client.post(
        f"/topics/{room_id}/task-proposals/{block_id}/dismiss",
        headers=session_auth_headers("alice"),
    )
    assert dismissed.status_code == 200, dismissed.text

    r = client.post(
        f"/topics/{room_id}/task-proposals/{block_id}/accept",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
    assert client.get(f"/topics/{room_id}/tasks").json()["data"]["data"] == []


def test_an_ai_teammate_cannot_accept_its_own_proposal(client):
    _project_id, room_id = _room(client)
    agent = room_agent_headers(client, room_id)
    block_id = client.post(
        f"/topics/{room_id}/task-proposals", json={"title": "自己批"}, headers=agent
    ).json()["data"]["id"]

    r = client.post(
        f"/topics/{room_id}/task-proposals/{block_id}/accept", headers=agent
    )

    assert r.status_code == 403
    assert client.get(f"/topics/{room_id}/tasks").json()["data"]["data"] == []


# —— 开始 ————————————————————————————————————————————————————————————————


def test_only_the_owner_starts_a_task(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    task = open_task(client, room_id, owner="alice", start=False)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("bob"),
    )

    assert r.status_code == 403
    assert _task(client, room_id, task["id"])["started_at"] is None


def test_a_task_starts_once(client):
    _project_id, room_id = _room(client)
    task = open_task(client, room_id)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 422


def test_starting_with_nobody_to_review_is_refused(client):
    """Changes nobody will review cannot be started on; the owner is asked to
    name someone rather than the task starting with a gap where review goes."""
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 422
    assert _task(client, room_id, task["id"])["started_at"] is None


def test_starting_records_what_the_document_said_then(client):
    """Review compares the document now with the document at the start, so
    the start must pin a version that later edits do not move."""
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)
    doc_id = client.get(
        f"/topics/{task['id']}/document",
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    wrote = client.put(
        f"/documents/{doc_id}",
        json={"content": "# 目标\n\n先做导出。", "expected_version": 0},
        headers=session_auth_headers("alice"),
    )
    assert wrote.status_code == 200, wrote.text

    started = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    at_start = started["started_doc_version"]
    rewrote = client.put(
        f"/documents/{doc_id}",
        json={"content": "# 目标\n\n先做导入。", "expected_version": at_start},
        headers=session_auth_headers("alice"),
    )
    assert rewrote.status_code == 200, rewrote.text

    compared = client.get(
        f"/documents/{doc_id}/compare",
        params={"before": at_start},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    assert "导出" in compared["before"]["content"]
    assert "导入" in compared["after"]["content"]
    assert _task(client, room_id, task["id"])["started_doc_version"] == at_start


def test_a_change_answers_with_the_task_as_its_page_reads_it(client):
    """The task page replaces what it shows with what a start, a hand-over or
    a close answers; an answer without the task's board cell left the page
    with nothing to render. Each answer says what reading the task says."""
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)
    alice = session_auth_headers("alice")

    def read() -> dict:
        r = client.get(f"/topics/{task['id']}/task", headers=alice)
        return r.json()["data"]["presentation"]

    def answers_as_read(change) -> None:
        # The start's message reaches the task's session on its own time, so
        # the task may begin running between the answer and a read of it: the
        # answer agrees with the page as it stood just before or just after.
        before = read()
        answered = change().json()["data"]["presentation"]
        assert answered in (before, read())

    answers_as_read(
        lambda: client.post(
            f"/topics/{task['id']}/start",
            json={"reviewer_handle": "alice"},
            headers=alice,
        )
    )
    answers_as_read(
        lambda: client.patch(
            f"/topics/{task['id']}/task", json={"agent_handle": None}, headers=alice
        )
    )
    answers_as_read(
        lambda: client.post(
            f"/topics/{task['id']}/close", json={"conclusion": "做完了"}, headers=alice
        )
    )


# —— 在任务里说话 ——————————————————————————————————————————————————————————


def test_only_the_owner_talks_in_a_task(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    task = open_task(client, room_id, owner="alice", start=False)

    by_bob = _say_in_task(client, room_id, task["id"], session_auth_headers("bob"))
    by_room_agent = _say_in_task(
        client, room_id, task["id"], room_agent_headers(client, room_id)
    )

    assert by_bob.status_code == 403
    assert by_room_agent.status_code == 403


def test_what_is_said_in_a_task_stays_out_of_the_room(client):
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)

    r = _say_in_task(
        client, room_id, task["id"], session_auth_headers("alice"), "只在任务里说"
    )
    assert r.status_code == 200, r.text

    assert "只在任务里说" not in _room_contents(client, room_id)
    timeline = _task_contents(client, task["id"])
    assert "只在任务里说" in timeline


def test_a_tasks_own_session_speaks_in_its_task_and_no_other(client):
    project_id, room_id = _room(client)
    mine = open_task(client, room_id, "我的", start=False)
    other = open_task(client, room_id, "别的", start=False)
    headers = _task_session_headers(client, project_id, room_id, mine["id"])

    in_mine = _say_in_task(client, room_id, mine["id"], headers, "我来写初稿")
    in_other = _say_in_task(client, room_id, other["id"], headers, "串门")
    closing_other = client.post(
        f"/topics/{other['id']}/close",
        json={"conclusion": "替你关了"},
        headers=headers,
    )

    assert in_mine.status_code == 200, in_mine.text
    assert in_other.status_code == 403
    assert closing_other.status_code == 403
    assert _task(client, room_id, other["id"])["status"] == "open"


def test_a_closed_task_takes_no_more_messages(client):
    _project_id, room_id = _room(client)
    task = open_task(client, room_id)
    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "结论：不做了，原方案够用"},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text

    r = _say_in_task(client, room_id, task["id"], session_auth_headers("alice"))

    assert r.status_code == 422


def test_only_the_owner_closes_a_task(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    task = open_task(client, room_id, owner="alice")

    r = client.post(
        f"/topics/{task['id']}/close",
        json={},
        headers=session_auth_headers("bob"),
    )

    assert r.status_code == 403
    assert _task(client, room_id, task["id"])["status"] == "open"


# —— 任务的文档 ————————————————————————————————————————————————————————————


def test_others_read_a_tasks_document_and_cannot_write_it(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    task = open_task(client, room_id, owner="alice", start=False)
    doc_id = client.get(
        f"/topics/{task['id']}/document",
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    client.put(
        f"/documents/{doc_id}",
        json={"content": "# 目标\n\n一句话。", "expected_version": 0},
        headers=session_auth_headers("alice"),
    )

    read = client.get(f"/documents/{doc_id}", headers=session_auth_headers("bob"))
    write = client.put(
        f"/documents/{doc_id}",
        json={"content": "我改一下", "expected_version": 1},
        headers=session_auth_headers("bob"),
    )

    assert read.status_code == 200
    assert "一句话" in read.json()["data"]["content"]
    assert write.status_code == 403
    still = client.get(f"/documents/{doc_id}", headers=session_auth_headers("alice"))
    assert "一句话" in still.json()["data"]["content"]


def test_a_tasks_session_writes_its_own_document(client):
    project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)
    headers = _task_session_headers(client, project_id, room_id, task["id"])
    doc_id = client.get(f"/topics/{task['id']}/document", headers=headers).json()[
        "data"
    ]["id"]

    r = client.put(
        f"/documents/{doc_id}",
        json={"content": "# 目标\n\n初稿。", "expected_version": 0},
        headers=headers,
    )

    assert r.status_code == 200, r.text


# —— 任务自己的会话 ————————————————————————————————————————————————————————


class _Screen(StubChannel):
    """A screen that gives each conversation a session id of its own, and
    remembers which session each launch in a conversation was asked to
    resume."""

    def __init__(self, session_ids: dict[str, str]) -> None:
        super().__init__()
        self.session_ids = {uuid.UUID(k): v for k, v in session_ids.items()}
        self.resume_asked: dict[uuid.UUID, list[str | None]] = {}

    async def open(self, session, agent, launch):
        conversation = session.conversation_id
        self.resume_asked.setdefault(conversation, []).append(launch.resume_session_id)
        self.new_session_id = self.session_ids[conversation]
        return await super().open(session, agent, launch)

    def emit_turn(self, topic_id, prompt, reply, *, agent=None) -> None:
        sid = self.session_ids[topic_id]
        self.starts(topic_id, session_id=sid)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, reply)
        self.stops(topic_id, reply, session_id=sid)


def _service(client, tmp_path, screen) -> ChatService:
    return ChatService(
        session_factory=client.test_request_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


def _turn(client, svc: ChatService, conversation_id: str, content: str) -> None:
    cid = uuid.UUID(conversation_id)

    async def _go() -> None:
        async for _ in svc.converse(
            topic_id=cid, author="alice", content=content, summon=True
        ):
            pass
        await settle_turn(svc, cid)

    client.portal.call(_go)


def test_a_task_keeps_a_session_of_its_own(client, tmp_path):
    """The task's AI picks up its own conversation after a cold start, and
    working the task does not count as the room's AI having run."""
    project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)
    screen = _Screen({task["id"]: "s-task"})
    svc = _service(client, tmp_path, screen)

    _turn(client, svc, task["id"], "第一轮")
    _turn(client, svc, task["id"], "第二轮")

    assert screen.resume_asked[uuid.UUID(task["id"])] == [None, "s-task"]
    summary = client.get(
        f"/projects/{project_id}/topics/{room_id}/work-summary",
        headers=session_auth_headers("alice"),
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["data"]["has_run"] is False


def test_a_task_turn_answers_in_the_task(client, tmp_path):
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)

    screen = _Screen({task["id"]: "s-task"})
    screen.reply = "任务里的回答"
    _turn(client, _service(client, tmp_path, screen), task["id"], "做吧")

    replies = _task_contents(client, task["id"])
    assert any("任务里的回答" in r for r in replies), replies
    assert "任务里的回答" not in "".join(_room_contents(client, room_id))


def test_talking_in_the_room_does_not_reach_the_task(client):
    _project_id, room_id = _room(client)
    task = open_task(client, room_id)

    post_message(client, room_id, "alice", {"content": "房间里的话"})

    timeline = _task_contents(client, task["id"])
    assert "房间里的话" not in timeline


# —— 现场 ————————————————————————————————————————————————————————————————


def _step(client, project_id, room_id, task_id, content) -> str:
    """One step an AI session took, in the room's line or in a task's."""
    from app.domain.block.models import AuthorType, BlockKind
    from app.domain.block.repositories import BlockRepository

    author = room_agent_seat(client, room_id)

    async def _add() -> str:
        async with client.test_request_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(task_id or room_id),
                author=author,
                author_type=AuthorType.participant,
                content=content,
                kind=BlockKind.event,
                meta={"tool": "Bash", "output": f"{content} 的输出", "output_bytes": 9},
            )
            await session.commit()
            return str(block.id)

    return client.portal.call(_add)


def _site(client, room_id, task_id=None, handle="alice") -> list[str]:
    r = client.get(
        f"/topics/{task_id or room_id}/transcript",
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return [b["content"] for b in r.json()["data"]["data"]]


def test_a_tasks_site_shows_what_its_session_did_and_the_rooms_does_not(client):
    project_id, room_id = _room(client)
    task = open_task(client, room_id)
    _with_bob(client, project_id, room_id)
    _step(client, project_id, room_id, None, "房间里的一步")
    _step(client, project_id, room_id, task["id"], "任务里的一步")

    in_task, in_room = _site(client, room_id, task["id"]), _site(client, room_id)
    assert "任务里的一步" in in_task and "房间里的一步" not in in_task
    assert "房间里的一步" in in_room and "任务里的一步" not in in_room
    # Whoever can see the task can watch it being worked.
    assert "任务里的一步" in _site(client, room_id, task["id"], handle="bob")


def test_a_steps_output_is_read_through_the_conversation_it_belongs_to(client):
    project_id, room_id = _room(client)
    task = open_task(client, room_id)
    step = _step(client, project_id, room_id, task["id"], "任务里的一步")
    alice = session_auth_headers("alice")

    through_task = client.get(
        f"/topics/{task['id']}/transcript/{step}/output", headers=alice
    )
    assert through_task.status_code == 200, through_task.text
    assert through_task.json()["data"]["output"] == "任务里的一步 的输出"
    assert (
        client.get(
            f"/topics/{room_id}/transcript/{step}/output", headers=alice
        ).status_code
        == 404
    )


# —— 摆出来的东西与预览 ——————————————————————————————————————————————————————


def _show(client, room_id, headers, path, content):
    r = client.post(
        f"/topics/{room_id}/shown",
        json={"path": path, "content": content},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _shown(client, conversation_id) -> list[str]:
    r = client.get(
        f"/topics/{conversation_id}/shown", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return [item["path"] for item in r.json()["data"]["data"]]


def _preview(client, conversation_id):
    r = client.get(
        f"/topics/{conversation_id}/preview", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_what_a_task_shows_is_the_tasks_preview_not_the_rooms(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")
    project_id, room_id = _room(client)
    task = open_task(client, room_id)
    room_session = room_agent_headers(client, room_id)
    task_session = _task_session_headers(client, project_id, room_id, task["id"])
    _show(client, room_id, room_session, "room.html", "<p>房间</p>")

    # The task's session shows in its own conversation, as `cheese show` does.
    _show(client, task["id"], task_session, "task.html", "<p>任务</p>")

    assert _shown(client, task["id"]) == ["task.html"]
    assert _shown(client, room_id) == ["room.html"]
    assert _preview(client, task["id"])["path"] == "task.html"
    assert _preview(client, room_id)["path"] == "room.html"


def test_a_task_and_its_room_each_keep_their_own_preview_tunnel(client):
    """The same teammate serves an app in the room and in a task: the task's
    helper does not take the room's tunnel, nor the reverse."""
    from app.api.routes.app_preview import connection_hub

    project_id, room_id = _room(client)
    task = open_task(client, room_id)
    seat = room_agent_seat(client, room_id)
    room_token = mint_scoped_token(
        project_id=project_id, topic_id=room_id, agent_handle=seat
    )
    task_token = mint_scoped_token(
        project_id=project_id, topic_id=task["id"], agent_handle=seat
    )

    with client.websocket_connect(f"/preview/tunnel?token={room_token}") as room_ws:
        with client.websocket_connect(f"/preview/tunnel?token={task_token}"):
            hub = connection_hub(room_ws)
            assert hub.is_online(uuid.UUID(room_id), seat)
            assert hub.is_online(uuid.UUID(task["id"]), seat)
