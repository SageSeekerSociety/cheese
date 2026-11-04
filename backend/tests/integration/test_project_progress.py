"""项目总览的「最近进展」，以及任务的经过留在任务里。

- 进展从已有的事实算：任务建了、开始了、关掉了，读者都看得到；看不见的私密频道
  里的任务不进他的进展。
- 一个任务在频道主线上只占一行（那一行原地更新）；开始、关闭这些经过说在任务自己
  的对话里，不再一条条落到频道主线上。
"""

import uuid
from datetime import UTC, datetime

from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from tests.integration.conftest import (
    join_project_team,
    post_project,
    registered,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(
        client, json={"name": "P"}, headers=session_auth_headers("alice")
    ).json()["data"]["id"]


def _channel(client, project: str, *, private: bool = False) -> str:
    r = client.post(
        "/topics",
        json={
            "project_id": project,
            "title": "私下" if private else "前端",
            **({"members_only": True} if private else {}),
        },
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _task(client, channel: str, title: str) -> str:
    async def seed() -> uuid.UUID:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(channel))
            task = await TaskService(session).open_thread(
                project_id=room.project_id,
                room_id=room.id,
                title=title,
                owner_handle="alice",
                created_by="alice",
            )
            await session.commit()
            return task.id

    return str(client.portal.call(seed))


def _progress(client, project: str, handle: str) -> list[dict]:
    r = client.get(
        f"/projects/{project}/progress", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _said(client, place: str) -> list[str]:
    r = client.get(f"/topics/{place}/blocks", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return [(b.get("meta") or {}).get("action") or "" for b in r.json()["data"]["data"]]


def test_what_happened_to_a_task_is_in_the_project_progress(client):
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "报名表单字段精简")
    r = client.post(
        f"/topics/{task}/close",
        json={"conclusion": "字段删掉了"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    kinds = {(h["kind"], h["taskId"]) for h in _progress(client, project, "alice")}
    assert ("created", task) in kinds
    assert ("completed", task) in kinds


def test_a_private_channels_tasks_stay_out_of_an_outsiders_progress(client):
    project = _project(client)
    join_project_team(client, project, "bob")
    hidden = _task(client, _channel(client, project, private=True), "私下的事")
    shown = _task(client, _channel(client, project), "公开的事")

    seen_by_bob = {h["taskId"] for h in _progress(client, project, "bob")}
    assert shown in seen_by_bob
    assert hidden not in seen_by_bob
    assert hidden in {h["taskId"] for h in _progress(client, project, "alice")}


def _card_merged_on_github(client, channel: str, task: str, merged_by: dict) -> None:
    """一张在 GitHub 上被直接合掉的卡：没有平台采纳人，只有 GitHub 合并人。"""
    from app.domain.review.models import AcceptCard, AcceptStatus

    async def seed() -> None:
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(channel),
                    task_id=uuid.UUID(task),
                    reviewer_handle="alice",
                    status=AcceptStatus.accepted,
                    decided_by=None,
                    pr_number=9,
                    pr_url="https://github.com/acme/widgets/pull/9",
                    pr_merged_at=datetime.now(UTC),
                    merged_by=merged_by,
                )
            )
            await session.commit()

    client.portal.call(seed)


def _accept_by(client, project: str, task: str) -> list[tuple[str | None, bool]]:
    return [
        (h["by"], h["byGithub"])
        for h in _progress(client, project, "alice")
        if h["kind"] == "accepted" and h["taskId"] == task
    ]


def test_an_accept_merged_on_github_names_the_linked_platform_member(client):
    """GitHub 数字 id 查得到平台成员：显示平台 handle，不显示 GitHub 登录名。"""
    from app.domain.oauth.repositories import OAuthConnectionRepository

    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "直接合的那次")
    _card_merged_on_github(
        client, channel, task, {"github_id": "5150", "login": "bob-gh"}
    )

    async def link() -> None:
        async with client.test_factory() as session:
            bob = await registered(session, "bob")
            await OAuthConnectionRepository(session).create(
                user_id=bob, provider_id="github_app", provider_user_id="5150"
            )
            await session.commit()

    client.portal.call(link)

    assert _accept_by(client, project, task) == [("bob", False)]


def test_an_accept_merged_on_github_falls_back_to_the_github_login(client):
    """查不到平台成员：显示 GitHub 登录名，不拿负责人顶。"""
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "没人认得的那次")
    _card_merged_on_github(
        client, channel, task, {"github_id": "999", "login": "someone-gh"}
    )

    assert _accept_by(client, project, task) == [("someone-gh", True)]


def test_closing_a_task_is_said_in_the_task_not_the_channel(client):
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "首页加载慢")

    r = client.post(
        f"/topics/{task}/close",
        json={"conclusion": "已经改好"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert "task_closed" not in _said(client, channel)
    assert "task_closed" in _said(client, task)


def test_the_window_reads_only_work_that_is_open_or_moved_inside_it(client):
    """The feed reads open work and work touched inside its window, not the
    whole project: a task closed and left alone before the window cannot say
    anything there, and a project holds thousands of those."""
    from datetime import timedelta

    from sqlalchemy import text

    project = _project(client)
    channel = _channel(client, project)
    open_task = _task(client, channel, "还在做")
    old = _task(client, channel, "早就关了")
    recent = _task(client, channel, "刚关")
    long_ago = datetime.now(UTC) - timedelta(days=60)
    yesterday = datetime.now(UTC) - timedelta(days=1)

    async def age() -> set[uuid.UUID]:
        async with client.test_factory() as session:
            for task_id, at in ((old, long_ago), (recent, yesterday)):
                await session.execute(
                    text(
                        "UPDATE tasks SET status = 'closed', created_at = :old,"
                        " updated_at = :at, closed_at = :at WHERE id = :id"
                    ),
                    {"old": long_ago, "at": at, "id": uuid.UUID(task_id)},
                )
            await session.commit()
            rows = await TaskService(session).list_in_project(
                uuid.UUID(project),
                open_or_touched_since=datetime.now(UTC) - timedelta(days=14),
            )
            return {t.id for t in rows}

    read = client.portal.call(age)
    assert uuid.UUID(open_task) in read
    assert uuid.UUID(recent) in read
    assert uuid.UUID(old) not in read
