"""A task delivers in steps: only the step its card calls the last closes it.

A plan of three steps used to end at the first: accepting step one closed the
task, and the other two had nowhere to go (dev, 2026-10-07).
"""

import uuid

import pytest
from sqlalchemy import text

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_accept_pr import (
    _accept,
    _give_card_a_pr,
    _make_project,
    _make_topic,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)
from tests.landing import summary_turn_ends, time_passes, watch
from tests.machine_work import machine_commits
from tests.support import git_store

app_world = pytest.fixture(_app_world_fixture.__wrapped__)  # type: ignore[attr-defined]


def _file(client, topic_id: str, *, completes_task: bool) -> str:
    r = client.post(
        f"/topics/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "feat(form): trim the sign-up form",
            "reviewer_handle": "alice",
            "completes_task": completes_task,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["completes_task"] is completes_task
    return r.json()["data"]["id"]


def _accepted_step(client, app_world, *, completes_task: bool) -> tuple[str, dict]:
    fake = app_world["fake"]
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _file(client, tid, completes_task=completes_task)
    head = _give_card_a_pr(client, app_world, tid, cid, 7)
    fake.check_state_by_sha[head] = ("success", "全部通过")
    fake.merge_sha_by_number[7] = "merge-sha-1"
    task_id = delivery_task_id(client, tid)
    before = client.get(f"/topics/{task_id}/task").json()["data"]
    r = _accept(client, cid)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"
    return task_id, before


def _told(client, task_id: str) -> str:
    async def read():
        async with client.test_factory() as db:
            rows = await db.execute(
                text(
                    "SELECT payload->>'content' FROM deliveries "
                    "WHERE conversation_id = :task ORDER BY recorded_at"
                ),
                {"task": task_id},
            )
            return "\n".join(content or "" for (content,) in rows)

    return client.portal.call(read)


def test_accepting_a_step_that_is_not_the_last_keeps_the_task_going(client, app_world):
    task_id, before = _accepted_step(client, app_world, completes_task=False)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "open"
    assert task["presentation"]["phrase"] != "accepted"
    # The next step is made on a branch of its own, from the latest code, and
    # opens a PR of its own.
    assert task["branch_name"] != before["branch_name"]
    assert task["pr_number"] is None
    assert "接着做下一步" in _told(client, task_id)


def test_the_next_step_counts_only_its_own_changes(client, app_world):
    """After a step lands, 改动 is the next step's: its count is what that step
    changed, not the landed step's files again.

    The badge used to compare the landed step's head with the default branch, so
    a step that had changed nothing yet read 「改动 1」 over a body with nothing
    in it (dev, 2026-10-09)."""
    task_id, before = _accepted_step(client, app_world, completes_task=False)
    task_uuid = uuid.UUID(str(task_id))
    task = client.get(f"/topics/{task_id}/task").json()["data"]
    project = uuid.UUID(task["project_id"])
    # What the forge reported as the landed head: the step branch's own tip.
    landed = git_store.head(project, before["branch_name"])

    async def record_landed_head():
        async with client.test_factory() as db:
            await db.execute(
                text("UPDATE tasks SET delivered_head = :head WHERE id = :task"),
                {"head": landed, "task": task_uuid},
            )
            await db.commit()

    client.portal.call(record_landed_head)
    # The forge merges the step into main, and the machine moves the task's
    # checkout onto the next step's branch, cut from the latest code.
    git_store.merge_task(project, task_uuid, message="feat: step one")
    git_store.git(git_store.path(project), "branch", task["branch_name"], "main")
    git_store.bind_task(
        task_uuid,
        branch=task["branch_name"],
        directory=f"task_{task_uuid.hex[:8]}",
        base="main",
    )

    def changed() -> list[str]:
        r = client.get(
            f"/projects/{project}/topics/{task_id}/work-summary",
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["changed_files"]

    assert changed() == []
    machine_commits(project, task_uuid, {"step-two.txt": "two\n"}, "Step two")
    assert changed() == ["step-two.txt"]


def test_accepting_the_last_step_reads_as_accepted_and_has_the_task_written_up(
    client, app_world
):
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["presentation"]["phrase"] == "accepted"
    # Its AI teammate gets one turn to bring the document up to date and say
    # what came of the task.
    told = _told(client, task_id)
    assert "最后一次交付已经采纳并合并" in told
    assert "实况文档" in told and "chat_send" in told


def test_the_task_closes_when_its_write_up_turn_ends(client, app_world):
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    summary_turn_ends(client, task_id)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_the_task_closes_without_a_write_up_that_never_comes(client, app_world):
    task_id, _ = _accepted_step(client, app_world, completes_task=True)

    time_passes(client, task_id, minutes=50)
    watch(client)

    task = client.get(f"/topics/{task_id}/task").json()["data"]
    assert task["status"] == "closed"
    assert task["presentation"]["phrase"] == "accepted"


def test_the_owner_reopens_a_completed_task_onto_the_latest_code(client, app_world):
    task_id, before = _accepted_step(client, app_world, completes_task=True)
    summary_turn_ends(client, task_id)
    owner = client.get(f"/topics/{task_id}/task").json()["data"]["owner_handle"]

    refused = client.post(
        f"/topics/{task_id}/reopen", headers=session_auth_headers("someone-else")
    )
    assert refused.status_code in (403, 404)

    r = client.post(f"/topics/{task_id}/reopen", headers=session_auth_headers(owner))
    assert r.status_code == 200, r.text
    task = r.json()["data"]
    assert task["status"] == "open"
    assert task["branch_name"] != before["branch_name"]
    assert task["pr_number"] is None


def test_an_open_task_cannot_be_reopened(client):
    from tests.integration.test_tasks import _room

    _project, room = _room(client)
    task = client.post(
        f"/topics/{room}/tasks",
        json={"title": "一件事"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    r = client.post(
        f"/topics/{task['id']}/reopen", headers=session_auth_headers("alice")
    )

    assert 400 <= r.status_code < 500
    assert client.get(f"/topics/{task['id']}/task").json()["data"]["status"] == "open"


def test_how_a_task_ended_is_said_where_it_came_from(client):
    from tests.integration.test_tasks import _room

    _project, room = _room(client)
    said = post_message(client, room, "alice", {"content": "报名表单太长了"})
    task = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    ).json()["data"]

    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "必填项压到 4 项"},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text

    threads = client.get(f"/topics/{room}/threads").json()["data"]
    [thread] = [t for t in threads if t["root_block_id"] == said["id"]]
    replies = client.get(f"/topics/{thread['id']}/blocks").json()["data"]["data"]
    assert any("必填项压到 4 项" in b["content"] for b in replies)


def test_a_task_from_something_that_is_not_a_message_still_closes(client):
    """The discussion hears how a task ended only where there is one; a task
    made from a file in the channel closes all the same."""
    import asyncio
    import uuid

    from app.domain.block.models import AuthorType, Block, BlockKind
    from tests.integration.test_tasks import _room

    project, room = _room(client)
    file_id = uuid.uuid4()

    async def put_file():
        async with client.test_factory() as session:
            session.add(
                Block(
                    id=file_id,
                    project_id=uuid.UUID(project),
                    conversation_id=uuid.UUID(room),
                    kind=BlockKind.attachment,
                    author_type=AuthorType.participant,
                    author="alice",
                    content="海报.png",
                )
            )
            await session.commit()

    asyncio.run(put_file())
    task = client.post(
        f"/blocks/{file_id}/upgrade", headers=session_auth_headers("alice")
    ).json()["data"]

    closed = client.post(
        f"/topics/{task['id']}/close",
        json={"conclusion": "海报定稿"},
        headers=session_auth_headers("alice"),
    )

    assert closed.status_code == 200, closed.text
    assert client.get(f"/topics/{task['id']}/task").json()["data"]["status"] == "closed"
