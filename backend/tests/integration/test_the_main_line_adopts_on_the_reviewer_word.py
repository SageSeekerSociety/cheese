"""A channel's main line adopts a delivery only on its reviewer's own word.

The rules, as a person would state them:

- When the reviewer tells the main line to adopt (「合并吧」), it adopts for
  them, and the delivery is recorded as adopted by the AI teammate on that
  person's behalf.
- Anyone else saying it — another member, or the platform waking the main
  line — adopts nothing; the delivery keeps waiting for its reviewer.
- A version handed in after the reviewer spoke is one they have not seen, and
  is not adopted on that word.
"""

import uuid
from datetime import UTC, datetime

from app.domain.agent.models import AgentTurn
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from app.domain.room_task.models import Task
from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.landing import written_up


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = project["root_topic_id"]
    return project["id"], room, room_agent_seat(client, room)


def _as_main_line(client, project: str, room: str, seat: str, method: str, path: str):
    token = mint_scoped_token(project_id=project, topic_id=room, agent_handle=seat)
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method, path, json=None, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def _task_with_delivery(client, project: str, room: str, seat: str) -> dict:
    task = client.post(
        f"/topics/{room}/tasks",
        json={"title": "首页加报名表", "owner_handle": "alice"},
        headers=session_auth_headers("alice"),
    ).json()["data"]

    async def deliver():
        async with client.test_factory() as db:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task["id"]),
                reviewer_handle="alice",
                status=AcceptStatus.pending,
                change_subject="首页加报名表",
                deliverable_kind=DeliverableKind.link,
                deliverable_url="https://example.edu/signup",
            )
            db.add(card)
            await db.commit()
            return card.id

    task["card"] = client.portal.call(deliver)
    return task


def _say_then_wake(client, room: str, seat: str, author: str, words: str) -> None:
    """``author`` says ``words`` in the main line, and the main line's turn for
    that message is running, named by it as the runtime names a turn a
    person's message starts (``author`` is ``system`` for a turn the platform
    began, which no message started)."""
    said = (
        uuid.UUID(post_message(client, room, author, {"content": words})["id"])
        if author != "system"
        else uuid.uuid4()
    )

    async def wake():
        async with client.test_factory() as db:
            db.add(
                AgentTurn(
                    id=uuid.uuid4(),
                    conversation_id=uuid.UUID(room),
                    continuation_id=said,
                    agent_handle=seat,
                    author=author,
                    content=words,
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    client.portal.call(wake)


def _card(client, card_id) -> AcceptCard:
    async def read():
        async with client.test_factory() as db:
            return await db.get(AcceptCard, card_id)

    return client.portal.call(read)


def _accept(client, project, room, seat, task):
    return _as_main_line(
        client,
        project,
        room,
        seat,
        "POST",
        f"/topics/{room}/tasks/{task['number']}/accept",
    )


def test_the_reviewer_saying_merge_adopts_on_their_behalf(client, stub_project_forge):
    project, room, seat = _channel(client)
    task = _task_with_delivery(client, project, room, seat)
    _say_then_wake(client, room, seat, "alice", "可以了 合并吧")

    adopted = _accept(client, project, room, seat, task)

    assert adopted.status_code == 200, adopted.text
    card = _card(client, task["card"])
    assert card.status == AcceptStatus.accepted
    assert card.decided_by == "alice"
    assert card.decided_by_assistant is True


def test_another_member_saying_merge_adopts_nothing(client, stub_project_forge):
    project, room, seat = _channel(client)
    task = _task_with_delivery(client, project, room, seat)
    _say_then_wake(client, room, seat, "bob", "合并吧")

    refused = _accept(client, project, room, seat, task)

    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "coordinateAcceptReviewerWordOnly"
    assert _card(client, task["card"]).status == AcceptStatus.pending


def test_a_turn_the_platform_began_adopts_nothing(client, stub_project_forge):
    project, room, seat = _channel(client)
    task = _task_with_delivery(client, project, room, seat)
    _say_then_wake(client, room, seat, "system", "任务提交了第 1 版")

    refused = _accept(client, project, room, seat, task)

    assert refused.status_code == 403, refused.text
    assert _card(client, task["card"]).status == AcceptStatus.pending


def test_a_version_handed_in_after_the_word_is_not_adopted(client, stub_project_forge):
    project, room, seat = _channel(client)
    _say_then_wake(client, room, seat, "alice", "合并吧")
    task = _task_with_delivery(client, project, room, seat)

    refused = _accept(client, project, room, seat, task)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "coordinateAcceptNewerDelivery"
    assert _card(client, task["card"]).status == AcceptStatus.pending


def test_where_it_came_from_says_the_teammate_adopted_it_for_the_reviewer(
    client, stub_project_forge
):
    """Once the task is written up, the main line's record of its end names the
    reviewer and the teammate that adopted it for them, by the teammate's own
    name in the project."""
    project, room, seat = _channel(client)
    task = _task_with_delivery(client, project, room, seat)
    asked = post_message(client, room, "alice", {"content": "首页加个报名表"})

    async def hang_under_the_request():
        async with client.test_factory() as db:
            row = await db.get(Task, uuid.UUID(task["id"]))
            row.upgraded_from_block_id = row.source_block_id = uuid.UUID(asked["id"])
            await db.commit()

    client.portal.call(hang_under_the_request)

    async def teammate_reports():
        async with client.test_factory() as db:
            await BlockRepository(db).add(
                project_id=uuid.UUID(project),
                conversation_id=uuid.UUID(room),
                author=seat,
                author_type=AuthorType.participant,
                content="报名表做好了",
                meta={"task_tag": task["id"]},
                own_output=True,
            )
            await db.commit()

    client.portal.call(teammate_reports)
    _say_then_wake(client, room, seat, "alice", "可以了 合并吧")
    assert _accept(client, project, room, seat, task).status_code == 200

    written_up(client, task["id"])

    lines = client.get(
        f"/topics/{room}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    [ended] = [
        b for b in lines if (b.get("meta") or {}).get("event_type") == "task_ended"
    ]
    assert ended["content"] == f"<@{seat}>代 <@alice> 采纳了「{task['title']}」"
