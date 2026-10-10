"""A task: one person's piece of work, talked through with its AI teammate in a
conversation of its own, beside the room it came from.

The rules a person could state before any of this was built:

- a person creates a task, and owns it; an AI teammate may only propose one;
- only the people working the task talk in it — its owner and the
  collaborators the owner brought in; everyone else says what they have to
  say in the room;
- nothing is changed in the project until the owner starts the task, and what
  the task's document said then is what its changes are reviewed against;
- the task's conversation and its AI session are its own: talking in it leaves
  the room's history and the room's session alone, and the reverse;
- a task's document is readable by whoever can see the task and written only
  by the people working it and its own session;
- a closed task takes no more messages.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.api import deps as session_turn_deps
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    in_thread,
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


def test_a_room_lists_each_task_with_its_newest_lines(client):
    """`limit` is per task: each task brings its own newest lines, so one long
    conversation never crowds out another's."""
    project_id, room_id = _room(client)
    long = open_task(client, room_id, "长的那件", start=False)
    short = open_task(client, room_id, "短的那件", start=False)

    async def say(task_id: str, lines: list[str]) -> None:
        start = datetime.now(UTC)
        async with client.test_factory() as db:
            for n, line in enumerate(lines):
                await BlockRepository(db).add(
                    project_id=uuid.UUID(project_id),
                    conversation_id=uuid.UUID(task_id),
                    author="alice",
                    author_type=AuthorType.participant,
                    content=line,
                    created_at=start + timedelta(seconds=n),
                )
            await db.commit()

    client.portal.call(say, long["id"], ["一", "二", "三"])
    client.portal.call(say, short["id"], ["就一句"])

    listed = client.get(
        f"/topics/{room_id}/tasks",
        params={"limit": 2},
        headers=session_auth_headers("alice"),
    )

    assert listed.status_code == 200, listed.text
    said = {
        t["id"]: [b["content"] for b in t["blocks"] if b["kind"] == "message"]
        for t in listed.json()["data"]["data"]
    }
    assert said[long["id"]] == ["二", "三"]
    assert said[short["id"]] == ["就一句"]


def test_an_ai_teammate_cannot_create_a_task(client):
    _project_id, room_id = _room(client)

    r = client.post(
        f"/topics/{room_id}/tasks",
        json={"title": "我自己开一个"},
        headers=room_agent_headers(client, room_id),
    )

    assert r.status_code == 403
    assert client.get(f"/topics/{room_id}/tasks").json()["data"]["data"] == []


def _teammate_in(client, room_id: str, thread_id: str) -> dict[str, str]:
    """The credential the room's AI teammate answers with in that 支线."""
    project_id = client.get(f"/topics/{room_id}").json()["data"]["project_id"]
    token = mint_scoped_token(
        project_id=project_id,
        topic_id=thread_id,
        agent_handle=room_agent_seat(client, room_id),
    )
    return {"X-Cheese-Token": token}


def _default_reviewer(client, project_id: str, handle: str) -> None:
    r = client.put(
        f"/projects/{project_id}/branch-protection",
        json={"default_reviewer": handle},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _tasks(client, room_id: str) -> list[dict]:
    return client.get(f"/topics/{room_id}/tasks").json()["data"]["data"]


def test_a_task_someone_asked_for_is_theirs_and_starts_at_once(client):
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    _default_reviewer(client, project_id, "alice")
    thread = in_thread(client, room_id, "alice")

    r = client.post(
        f"/topics/{thread}/teammate-tasks",
        json={
            "title": "迁移旧数据",
            "summary": "把旧表搬到新表",
            "owner_handle": "bob",
            "start": True,
        },
        headers=_teammate_in(client, room_id, thread),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["started"] is True
    [task] = _tasks(client, room_id)
    assert task["owner_handle"] == "bob"
    assert task["started_at"] is not None
    assert task["presentation"]["phrase"] != "discussing"


def test_a_teammates_own_idea_waits_for_its_owner_to_start(client):
    project_id, room_id = _room(client)
    _default_reviewer(client, project_id, "alice")
    thread = in_thread(client, room_id, "alice")

    r = client.post(
        f"/topics/{thread}/teammate-tasks",
        json={"title": "顺手重构", "summary": "把重复的两段合成一个函数"},
        headers=_teammate_in(client, room_id, thread),
    )

    assert r.status_code == 200, r.text
    [task] = _tasks(client, room_id)
    # Nobody named: whoever wrote the message the 支线 hangs under.
    assert task["owner_handle"] == "alice"
    assert task["started_at"] is None


def test_one_message_becomes_several_tasks_under_it(client):
    project_id, room_id = _room(client)
    thread = in_thread(client, room_id, "alice")
    root = client.get(f"/topics/{thread}/thread").json()["data"]["root"]["id"]
    agent = _teammate_in(client, room_id, thread)

    for title in ("表单字段精简", "学号格式校验"):
        r = client.post(
            f"/topics/{thread}/teammate-tasks",
            json={"title": title, "summary": "按支线里定的做"},
            headers=agent,
        )
        assert r.status_code == 200, r.text
    made = client.post(f"/blocks/{root}/upgrade", headers=session_auth_headers("alice"))
    assert made.status_code == 200, made.text

    tasks = _tasks(client, room_id)
    assert len(tasks) == 3
    assert {t["upgraded_from_block_id"] for t in tasks} == {root}
    # None of them adds a line to the channel's main line.
    main_line = client.get(f"/topics/{room_id}/blocks").json()["data"]["data"]
    assert not [b for b in main_line if (b.get("meta") or {}).get("task_id")]


def _told(client, task_id: str) -> str:
    """Everything the platform has handed the task's own session so far."""

    async def read():
        async with client.test_factory() as db:
            rows = await db.execute(
                text(
                    "SELECT payload->>'content' FROM deliveries "
                    "WHERE conversation_id = :task ORDER BY recorded_at"
                ),
                {"task": task_id},
            )
            return "\n".join(content for (content,) in rows)

    return client.portal.call(read)


def test_a_task_a_teammate_created_is_handed_the_discussion_behind_it(client):
    """The task's own session never reads the room, so what was agreed there
    reaches the task when it is created or not at all. On dev (2026-10-05) a
    task proposed with no summary started knowing none of the five changes
    agreed in its room."""
    _project_id, room_id = _room(client)
    thread = in_thread(client, room_id, "alice")
    post_message(client, thread, "alice", {"content": "删场次的通知选 B：发布时一起发"})
    task = client.post(
        f"/topics/{thread}/teammate-tasks",
        json={"title": "通知改到发布时", "summary": "发布时按人汇总变更"},
        headers=_teammate_in(client, room_id, thread),
    ).json()["data"]

    told = _told(client, task["id"])
    assert "发布时按人汇总变更" in told
    assert "删场次的通知选 B" in told


def test_a_task_that_says_nothing_of_the_work_is_refused(client):
    _project_id, room_id = _room(client)
    thread = in_thread(client, room_id, "alice")

    r = client.post(
        f"/topics/{thread}/teammate-tasks",
        json={"title": "顺手重构", "summary": ""},
        headers=_teammate_in(client, room_id, thread),
    )

    assert 400 <= r.status_code < 500
    assert _tasks(client, room_id) == []


def test_a_person_creates_tasks_their_own_way_not_as_a_teammate(client):
    _project_id, room_id = _room(client)
    thread = in_thread(client, room_id, "alice")

    r = client.post(
        f"/topics/{thread}/teammate-tasks",
        json={"title": "自己批", "summary": "改一处文案"},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 403
    assert _tasks(client, room_id) == []


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


def test_a_task_a_project_names_nobody_to_review_goes_back_to_its_owner(client):
    """The owner who starts it is who reviews it, when neither they nor the
    project named anyone. Being made to pick before the task could begin asked
    every owner the same question in a project where the answer is nearly
    always themselves."""
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "alice"
    assert _task(client, room_id, task["id"])["started_at"] is not None


def test_the_projects_default_reviewer_still_beats_the_owner(client):
    """A project that took the trouble to name a default reviewer means it: the
    owner fallback is the floor under that setting, not an override of it."""
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    _default_reviewer(client, project_id, "bob")
    task = open_task(client, room_id, start=False)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "bob"


def test_who_the_owner_names_beats_the_default_reviewer(client):
    """Naming someone still comes first: the person starting knows which change
    this is and who understands that part of it."""
    project_id, room_id = _room(client)
    _with_bob(client, project_id, room_id)
    _default_reviewer(client, project_id, "bob")
    task = open_task(client, room_id, start=False)

    r = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["reviewer_handle"] == "alice"


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
    with nothing to render. Each answer puts the task in the column reading
    it does. The phrase is not compared: a started task's session may begin
    its first turn between the answer and the read, and running is its own
    phrase."""
    _project_id, room_id = _room(client)
    task = open_task(client, room_id, start=False)
    alice = session_auth_headers("alice")

    def read() -> str:
        r = client.get(f"/topics/{task['id']}/task", headers=alice)
        return r.json()["data"]["presentation"]["column"]

    started = client.post(
        f"/topics/{task['id']}/start", json={"reviewer_handle": "alice"}, headers=alice
    ).json()["data"]
    assert started["presentation"]["column"] == read()
    handed = client.patch(
        f"/topics/{task['id']}/task", json={"agent_handle": None}, headers=alice
    ).json()["data"]
    assert handed["presentation"]["column"] == read()
    closed = client.post(
        f"/topics/{task['id']}/close", json={"conclusion": "做完了"}, headers=alice
    ).json()["data"]
    assert closed["presentation"]["column"] == read()


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


def _seated(client, project_id: str, room_id: str, handle: str) -> None:
    """``handle`` is on the project's team and in this room's roster."""
    join_project_team(client, project_id, handle)
    r = client.post(
        f"/topics/{room_id}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text


def _set_collaborators(client, task_id, handles, by="alice"):
    return client.patch(
        f"/topics/{task_id}/task",
        json={"contributor_handles": handles},
        headers=session_auth_headers(by),
    )


def test_a_collaborator_talks_in_the_task_and_others_still_do_not(client):
    project_id, room_id = _room(client)
    _seated(client, project_id, room_id, "bob")
    _seated(client, project_id, room_id, "carol")
    task = open_task(client, room_id, owner="alice", start=False)

    added = _set_collaborators(client, task["id"], ["bob"])

    assert added.status_code == 200, added.text
    assert added.json()["data"]["contributor_handles"] == ["bob"]
    by_bob = _say_in_task(client, room_id, task["id"], session_auth_headers("bob"))
    assert by_bob.status_code == 200, by_bob.text
    by_carol = _say_in_task(client, room_id, task["id"], session_auth_headers("carol"))
    assert by_carol.status_code == 403


def test_only_the_owner_brings_collaborators_in(client):
    project_id, room_id = _room(client)
    _seated(client, project_id, room_id, "bob")
    _seated(client, project_id, room_id, "carol")
    task = open_task(client, room_id, owner="alice", start=False)
    assert _set_collaborators(client, task["id"], ["bob"]).status_code == 200

    # A collaborator cannot bring someone else in, nor start or hand it over.
    assert (
        _set_collaborators(client, task["id"], ["bob", "carol"], by="bob").status_code
        == 403
    )
    handed = client.patch(
        f"/topics/{task['id']}/task",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("bob"),
    )
    assert handed.status_code == 403
    # Someone outside the project cannot be made one.
    post_project(client, owner="dave")
    assert _set_collaborators(client, task["id"], ["dave"]).status_code == 422
    assert _task(client, room_id, task["id"])["contributor_handles"] == ["bob"]


def test_a_collaborator_can_leave_and_then_no_longer_talks(client):
    project_id, room_id = _room(client)
    _seated(client, project_id, room_id, "bob")
    task = open_task(client, room_id, owner="alice", start=False)
    assert _set_collaborators(client, task["id"], ["bob"]).status_code == 200

    left = _set_collaborators(client, task["id"], [], by="bob")

    assert left.status_code == 200, left.text
    by_bob = _say_in_task(client, room_id, task["id"], session_auth_headers("bob"))
    assert by_bob.status_code == 403


def test_handing_a_task_to_its_collaborator_makes_them_its_owner_only(client):
    project_id, room_id = _room(client)
    _seated(client, project_id, room_id, "bob")
    task = open_task(client, room_id, owner="alice", start=False)
    assert _set_collaborators(client, task["id"], ["bob"]).status_code == 200

    handed = client.patch(
        f"/topics/{task['id']}/task",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )

    assert handed.status_code == 200, handed.text
    assert handed.json()["data"]["owner_handle"] == "bob"
    assert handed.json()["data"]["contributor_handles"] == []


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


def test_a_collaborator_writes_the_tasks_document(client):
    project_id, room_id = _room(client)
    _seated(client, project_id, room_id, "bob")
    task = open_task(client, room_id, owner="alice", start=False)
    assert _set_collaborators(client, task["id"], ["bob"]).status_code == 200
    doc_id = client.get(
        f"/topics/{task['id']}/document",
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]

    write = client.put(
        f"/documents/{doc_id}",
        json={"content": "# 目标\n\n协作者写的。", "expected_version": 0},
        headers=session_auth_headers("bob"),
    )

    assert write.status_code == 200, write.text


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
        work_runner=session_turn_deps.get_work_runner(),
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
