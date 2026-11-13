"""The project's files say which change last touched them, and which task it
came from (资料库 → 项目文件).

A change names its task only when the platform itself landed it: an accept
merged the task's pull request and recorded the commit it left. Anything else
on the branch — a push straight to it, a commit whose message names a task —
is a change with no task, shown by who and when.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from tests.delivery import delivery_task, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.machine_work import machine_commits
from tests.support import git_store


def _project(client) -> uuid.UUID:
    client.headers.update(session_auth_headers("alice"))
    return uuid.UUID(post_project(client, json={"name": "P"}).json()["data"]["id"])


def _channel_task(client, pid: uuid.UUID, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    r = client.post("/topics", json={"project_id": str(pid), "title": title})
    assert r.status_code == 200, r.text
    room = uuid.UUID(r.json()["data"]["id"])
    delivery_task(client, room, commit=False)
    task = delivery_task_id(client, room)

    async def name_it():
        async with client.test_factory() as session:
            (await session.get(Task, task)).title = title
            await session.commit()

    asyncio.run(name_it())
    return room, task


def _accept(client, pid, room, task, files: dict[str, str]) -> str:
    """A task's work, merged by an accept: the platform records the commit."""
    machine_commits(pid, task, files, "work on the branch")
    sha = git_store.merge_task(pid, task, message="feat: the task's change")

    async def record():
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=room,
                    task_id=task,
                    status=AcceptStatus.accepted,
                    pr_merged_at=datetime.now(UTC),
                    pr_head_sha=sha,
                )
            )
            await session.commit()

    asyncio.run(record())
    return sha


def _push_straight(pid, files: dict[str, str], message: str, author: str) -> str:
    repo = git_store.ensure_repo(pid)
    git_store.git(repo, "checkout", "-q", "main")
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
        git_store.git(repo, "add", name)
    git_store.git(
        repo,
        "-c",
        f"user.name={author}",
        "-c",
        "user.email=someone@example.test",
        "commit",
        "-qm",
        message,
    )
    return git_store.head(pid)


def _history(client, pid, headers=None, **params) -> list[dict]:
    r = client.get(f"/projects/{pid}/history", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"]["commits"]


def test_a_change_an_accept_landed_names_its_task(client):
    pid = _project(client)
    room, task = _channel_task(client, pid, "月度报告补趋势图")
    sha = _accept(client, pid, room, task, {"报告/月度报告.md": "# 十月\n"})

    latest = _history(client, pid, limit=1)[0]

    assert latest["sha"] == sha
    assert latest["task"] == {
        "id": str(task),
        "title": "月度报告补趋势图",
        "room_id": str(room),
    }
    assert latest["at"]


def test_a_commit_pushed_straight_names_no_task_even_when_its_message_does(client):
    pid = _project(client)
    room, task = _channel_task(client, pid, "清洗十月数据")
    _accept(client, pid, room, task, {"data.csv": "a,b\n"})
    _push_straight(
        pid,
        {"README.md": "hello\n"},
        f"docs: readme\n\nLink: https://example.test/projects/{pid}/topics/{room}/tasks/{task}",
        "陈默",
    )

    latest = _history(client, pid, limit=1)[0]

    assert latest["task"] is None
    assert latest["author"] == "陈默"


def test_a_files_history_is_the_changes_that_touched_it(client):
    pid = _project(client)
    room, task = _channel_task(client, pid, "改报告")
    report = _accept(client, pid, room, task, {"report.md": "v1\n"})
    _push_straight(pid, {"other.txt": "x\n"}, "chore: unrelated", "王珊")

    touched = _history(client, pid, path="report.md")

    assert [c["sha"] for c in touched] == [report]
    assert touched[0]["task"]["title"] == "改报告"


def test_a_private_channels_task_is_not_named_to_someone_outside_it(client):
    pid = _project(client)
    room, task = _channel_task(client, pid, "私下的任务")
    _accept(client, pid, room, task, {"secret.md": "x\n"})
    join_project_team(client, str(pid), "bob")

    async def make_private():
        async with client.test_factory() as session:
            (await session.get(Topic, room)).members_only = True
            # alice sits in the channel; bob is on the project but not in it.
            existing = await session.scalar(
                select(TopicMembership).where(
                    TopicMembership.topic_id == room,
                    TopicMembership.member_handle == "alice",
                )
            )
            if existing is None:
                session.add(
                    TopicMembership(
                        topic_id=room, member_handle="alice", role=TopicRole.member
                    )
                )
            await session.commit()

    asyncio.run(make_private())

    [seen_by_bob] = _history(
        client, pid, headers=session_auth_headers("bob"), path="secret.md"
    )
    [seen_by_alice] = _history(client, pid, path="secret.md")

    assert seen_by_bob["task"] is None, "a private channel's task reached a non-member"
    assert seen_by_alice["task"]["title"] == "私下的任务"


def test_a_path_outside_the_repository_is_refused(client):
    pid = _project(client)
    r = client.get(f"/projects/{pid}/history", params={"path": "../etc/passwd"})
    assert r.status_code == 422, r.text
