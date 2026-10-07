"""「需要我处理」：负责人那一条、归档的频道、决策请求、侧栏的点，以及停滞。

几条规则都是产品上先说定的（#2422 之后的「需要我处理」那一步）：

- 负责人只在三种时候算：芝士把任务文档写好了等他点「开始」、检查红了芝士清不掉、
  被退回之后没人接着改。只是任务开着不算。
- 归档的频道是只读的，里面的事不再等任何人。
- 芝士请他拍板而他还没拍的，在「待办」里，拍完就消失。
- 一个任务在不在等**看的这个人**，由服务端说：侧栏的点只对被等的那个人亮。
- 好几天没人动的任务：三天提醒负责人一次，十四天算「已停滞」；在等别人审阅或回答
  的不算。
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.delivery.models import Delivery
from app.domain.room_task.models import Task
from app.domain.room_task.services import TaskService
from app.domain.task_quiet.sweep import remind_quiet_tasks
from app.domain.topic.models import Topic, TopicStatus
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client, handle: str = "alice") -> str:
    return post_project(
        client, json={"name": "P"}, headers=session_auth_headers(handle)
    ).json()["data"]["id"]


def _channel(client, project: str, handle: str = "alice", title: str = "前端") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": title},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _task(client, channel: str, *, owner: str = "alice", started: bool) -> uuid.UUID:
    async def seed() -> uuid.UUID:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(channel))
            task = await TaskService(session).open_thread(
                project_id=room.project_id,
                room_id=room.id,
                title="报名表单字段精简",
                owner_handle=owner,
                created_by=owner,
            )
            if started:
                task.started_at = datetime.now(UTC)
                task.started_by = owner
                task.started_doc_version = 0
            await session.commit()
            return task.id

    return client.portal.call(seed)


def _age(client, task_id: uuid.UUID, days: int) -> None:
    """让这个任务看起来 `days` 天前建的、之后没人说过话。"""

    async def go() -> None:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            task.created_at = datetime.now(UTC) - timedelta(days=days)
            await session.commit()

    client.portal.call(go)


def _mine(client, handle: str) -> list[dict]:
    r = client.get("/awaiting-me", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _project_tasks(client, project: str, handle: str) -> dict[str, dict]:
    r = client.get(f"/projects/{project}/tasks", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()["data"]["data"]}


def test_an_owner_is_waited_on_to_start_a_task_whose_draft_is_ready(client):
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel, started=False)

    (item,) = _mine(client, "alice")
    assert item["taskId"] == str(task)
    assert item["reason"] == "owner"
    join_project_team(client, project, "bob")
    assert _mine(client, "bob") == []


def test_an_owner_is_not_waited_on_for_a_task_that_is_merely_open(client):
    project = _project(client)
    channel = _channel(client, project)
    _task(client, channel, started=True)

    assert _mine(client, "alice") == []


def test_nothing_in_an_archived_channel_waits_on_anyone(client):
    project = _project(client)
    channel = _channel(client, project)
    _task(client, channel, started=False)

    async def archive() -> None:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(channel))
            room.status = TopicStatus.archived
            await session.commit()

    client.portal.call(archive)
    assert _mine(client, "alice") == []


def test_a_decision_asked_of_me_waits_until_i_make_it(client):
    project = _project(client)
    channel = _channel(client, project)
    r = client.post(
        f"/projects/{project}/alerts",
        json={
            "level": "strong",
            "kind": "decision_request",
            "title": "上线前要不要先灰度",
            "body": "芝士给了两个方案",
            "target_handle": "alice",
            "topic_id": channel,
            "payload": {"options": ["先灰度", "直接上线"]},
        },
    )
    assert r.status_code == 200, r.text
    alert = r.json()["data"]["data"][0]

    (item,) = _mine(client, "alice")
    assert item["reason"] == "decide"
    assert item["topicId"] == channel
    assert item["question"] == "上线前要不要先灰度"
    assert item["options"] == ["先灰度", "直接上线"]

    r = client.post(
        f"/alerts/{alert['id']}/resolve",
        json={"chosen": "先灰度"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert _mine(client, "alice") == []


def test_a_task_waits_on_its_owner_alone_in_the_project_list(client):
    """侧栏的点按人亮：等负责人的任务，对别的成员不是「等我」。"""
    project = _project(client)
    join_project_team(client, project, "bob")
    channel = _channel(client, project)
    waiting = _task(client, channel, started=False)
    going = _task(client, channel, started=True)

    as_alice = _project_tasks(client, project, "alice")
    as_bob = _project_tasks(client, project, "bob")
    assert as_alice[str(waiting)]["awaits_me"] is True
    assert as_alice[str(going)]["awaits_me"] is False
    assert as_bob[str(waiting)]["awaits_me"] is False


def test_a_task_quiet_for_two_weeks_is_stalled_and_a_newer_one_is_not(client):
    project = _project(client)
    channel = _channel(client, project)
    old = _task(client, channel, started=True)
    _age(client, old, days=15)
    recent = _task(client, channel, started=True)
    _age(client, recent, days=4)

    rows = _project_tasks(client, project, "alice")
    assert rows[str(old)]["stalled"] is True
    assert rows[str(recent)]["stalled"] is False


def _quiet_notices(client, handle: str) -> list[str]:
    """投给 `handle` 的停滞提醒，各是哪个任务的。"""

    async def go() -> list[str]:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(Delivery).where(Delivery.recipient_handle == handle)
            )
            return [
                d.payload["taskId"]
                for d in rows.all()
                if (d.payload or {}).get("eventType") == "task_quiet"
            ]

    return client.portal.call(go)


def test_the_owner_of_a_task_quiet_for_three_days_is_reminded_once(client):
    seed_user(client, "alice")  # 通知落到一个真的用户名下
    project = _project(client)
    channel = _channel(client, project)
    quiet = _task(client, channel, started=True)
    _age(client, quiet, days=4)
    # 两周以上的已经收进「已停滞」了，不再提醒；刚动过的还不到时候。
    stalled = _task(client, channel, started=True)
    _age(client, stalled, days=20)
    _task(client, channel, started=True)

    client.portal.call(remind_quiet_tasks, client.test_factory)
    client.portal.call(remind_quiet_tasks, client.test_factory)

    assert _quiet_notices(client, "alice") == [str(quiet)]


def test_a_task_waiting_on_a_review_is_not_called_quiet(client):
    """球在审阅人手上：这件事不是没人管，负责人不该被催。"""
    from tests.delivery import delivery_headers, delivery_task_id

    seed_user(client, "alice")
    project = _project(client)
    channel = _channel(client, project)
    r = client.post(
        f"/topics/{delivery_task_id(client, channel)}/accept-card",
        headers=delivery_headers(client, channel),
        json={
            "change_subject": "chore(test): file a card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200, r.text
    task = delivery_task_id(client, channel)
    _age(client, task, days=20)

    async def silence() -> None:
        # 递卡在任务里说过话；把那几行也挪到二十天前。
        async with client.test_factory() as session:
            from app.domain.block.models import Block

            for block in (
                await session.scalars(
                    select(Block).where(Block.conversation_id == task)
                )
            ).all():
                block.created_at = datetime.now(UTC) - timedelta(days=20)
            await session.commit()

    client.portal.call(silence)

    assert _project_tasks(client, project, "alice")[str(task)]["stalled"] is False
    client.portal.call(remind_quiet_tasks, client.test_factory)
    assert _quiet_notices(client, "alice") == []
