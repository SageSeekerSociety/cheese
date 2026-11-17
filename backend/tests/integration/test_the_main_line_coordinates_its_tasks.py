"""A channel's main line coordinates the tasks it opens.

The rules, as a person would state them:

- It hands a task to another AI teammate only when a member named that
  teammate in the message the task is for.
- It tells a task what a member added; a request passed on for another member
  keeps that member, and the task hears it.
- It closes only a task it opened, and never one whose delivery waits on a
  person: closing would void it, which is the reviewer's decision.
- A task it opened reports a question back to it; one a person opened has no
  main line to report to.
"""

import uuid

from sqlalchemy import select

from app.domain.delivery.models import Delivery
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task
from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
    open_task,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], room, room_agent_seat(client, room)


def _as(
    client,
    project: str,
    conversation: str,
    seat: str,
    method: str,
    path: str,
    body=None,
):
    token = mint_scoped_token(
        project_id=project, topic_id=conversation, agent_handle=seat
    )
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _open(client, project, room, seat, **extra):
    made = _as(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/teammate-tasks",
        {
            "title": "登录页",
            "summary": "把登录页做好",
            "owner_handle": "alice",
            **extra,
        },
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]


def _told(client, task_id: str) -> list[dict]:
    async def read():
        async with client.test_factory() as session:
            return [
                dict(row.payload)
                for row in await session.scalars(
                    select(Delivery).where(
                        Delivery.conversation_id == uuid.UUID(task_id),
                        Delivery.payload["eventType"].as_string() == "task_told",
                    )
                )
            ]

    return client.portal.call(read)


def test_another_teammate_takes_a_task_only_when_a_member_named_it(client, stub_hooks):
    project, room, seat = _channel(client)
    post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})

    refused = _as(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/teammate-tasks",
        {"title": "登录页", "summary": "把登录页做好", "teammate": "cheese-other"},
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "teammateTaskNotNamed"


def test_a_request_passed_on_for_a_member_reaches_the_task_with_who_asked(
    client, stub_hooks
):
    project, room, seat = _channel(client)
    post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})
    task = _open(client, project, room, seat)

    told = _as(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/tasks/{task['number']}/tell",
        {"content": "按钮改成蓝色", "asker": "bob"},
    )

    assert told.status_code == 200, told.text
    [payload] = _told(client, task["id"])
    assert "按钮改成蓝色" in payload["content"]
    assert payload["asker"] == "bob"
    looked = _as(
        client, project, room, seat, "GET", f"/topics/{room}/tasks/{task['number']}"
    )
    assert looked.status_code == 200, looked.text
    assert f"#{task['number']}" in looked.json()["data"]["summary"]
    assert [row["kind"] for row in looked.json()["data"]["exchange"]] == ["task_told"]


def test_the_main_line_does_not_close_a_task_someone_else_opened(client, stub_hooks):
    project, room, seat = _channel(client)
    task = open_task(client, room, "别人开的", owner="alice")

    refused = _as(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/tasks/{task['number']}/close",
        {"conclusion": "不做了"},
    )

    assert refused.status_code == 403, refused.text


def test_the_main_line_does_not_close_a_task_awaiting_review(client, stub_hooks):
    project, room, seat = _channel(client)
    post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})
    task = _open(client, project, room, seat)

    async def waiting():
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(room),
                    task_id=uuid.UUID(task["id"]),
                    status=AcceptStatus.pending,
                )
            )
            await session.commit()

    client.portal.call(waiting)
    refused = _as(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/tasks/{task['number']}/close",
        {"conclusion": "不做了"},
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "coordinateCloseWhileReviewWaits"

    async def still_open():
        async with client.test_factory() as session:
            return str((await session.get(Task, uuid.UUID(task["id"]))).status)

    assert client.portal.call(still_open) == "open"


def test_a_task_reports_a_question_to_the_main_line_that_opened_it(client, stub_hooks):
    project, room, seat = _channel(client)
    post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})
    task = _open(client, project, room, seat)

    reported = _as(
        client,
        project,
        task["id"],
        seat,
        "POST",
        f"/topics/{task['id']}/report",
        {"kind": "question", "content": "按钮用什么颜色？"},
    )

    assert reported.status_code == 200, reported.text

    async def waiting():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery.payload["content"].astext).where(
                        Delivery.conversation_id == uuid.UUID(room),
                        Delivery.recipient_handle == seat,
                        Delivery.payload["eventType"].as_string() == "task_report",
                    )
                )
            )

    [said] = client.portal.call(waiting)
    assert "按钮用什么颜色？" in said
