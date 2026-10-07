"""项目总览的「最近进展」，以及任务的经过留在任务里。

- 进展从已有的事实算：任务建了、开始了、关掉了，读者都看得到；看不见的私密频道
  里的任务不进他的进展。
- 一个任务在频道主线上只占一行（那一行原地更新）；开始、关闭这些经过说在任务自己
  的对话里，不再一条条落到频道主线上。
"""

import uuid

from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from tests.integration.conftest import (
    join_project_team,
    post_project,
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
        f"/topics/{task}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    kinds = {(h["kind"], h["taskId"]) for h in _progress(client, project, "alice")}
    assert ("created", task) in kinds
    assert ("started", task) in kinds


def test_a_private_channels_tasks_stay_out_of_an_outsiders_progress(client):
    project = _project(client)
    join_project_team(client, project, "bob")
    hidden = _task(client, _channel(client, project, private=True), "私下的事")
    shown = _task(client, _channel(client, project), "公开的事")

    seen_by_bob = {h["taskId"] for h in _progress(client, project, "bob")}
    assert shown in seen_by_bob
    assert hidden not in seen_by_bob
    assert hidden in {h["taskId"] for h in _progress(client, project, "alice")}


def test_starting_and_closing_a_task_are_said_in_the_task_not_the_channel(client):
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, "首页加载慢")

    r = client.post(
        f"/topics/{task}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/topics/{task}/close",
        json={"conclusion": "已经改好"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert "task_started" not in _said(client, channel)
    assert "task_closed" not in _said(client, channel)
    assert "task_started" in _said(client, task)
    assert "task_closed" in _said(client, task)
