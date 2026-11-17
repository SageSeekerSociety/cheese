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
from datetime import UTC, datetime, timedelta

from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from app.domain.topic.models import Topic
from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
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
    """``author`` says ``words`` in the main line and the main line's turn for
    it is running (``author`` is ``system`` for a turn the platform began)."""
    now = datetime.now(UTC)

    async def seed():
        async with client.test_factory() as db:
            if author != "system":
                room_row = await db.get(Topic, uuid.UUID(room))
                db.add(
                    Block(
                        project_id=room_row.project_id,
                        conversation_id=uuid.UUID(room),
                        author=author,
                        author_type=AuthorType.participant,
                        kind=BlockKind.message,
                        content=words,
                        created_at=now,
                    )
                )
            turn = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn,
                    conversation_id=uuid.UUID(room),
                    continuation_id=turn,
                    agent_handle=seat,
                    author=author,
                    content=words,
                    started_at=now + timedelta(seconds=1),
                )
            )
            await db.commit()

    client.portal.call(seed)


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
