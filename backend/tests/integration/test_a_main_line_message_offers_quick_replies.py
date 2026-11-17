"""An AI teammate's main-line message can offer short answers under it.

The rules, as a person would state them:

- The answers are offered to the person it asks: the one it @-mentions, or
  else the one whose message it is answering.
- Clicking one is that person's own message replying to it; after that the
  message no longer offers its answers.
- Only the main line offers them: in a task, a question is asked as a question.
"""

import uuid
from datetime import UTC, datetime

from app.domain.agent.models import AgentTurn
from tests.integration.conftest import (
    mint_scoped_token,
    open_task,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, {"name": "Replies"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    return project["id"], room, room_agent_seat(client, room)


def _say(client, project_id: str, conversation: str, seat: str, body: dict):
    token = mint_scoped_token(
        project_id=project_id,
        topic_id=conversation,
        agent_handle=seat,
        access_scope="project",
    )
    return client.post(
        f"/topics/{conversation}/messages",
        json={"request_id": str(uuid.uuid4()), **body},
        headers={"Authorization": f"Bearer {token}"},
    )


def _message(client, room: str, message_id: str) -> dict:
    page = client.get(f"/topics/{room}/blocks", headers=session_auth_headers("alice"))
    return next(b for b in page.json()["data"]["data"] if b["id"] == message_id)


def _alice_is_answered(client, room: str, seat: str) -> None:
    """alice's message started the main line's running turn."""

    async def seed():
        async with client.test_factory() as db:
            turn = uuid.uuid4()
            db.add(
                AgentTurn(
                    id=turn,
                    conversation_id=uuid.UUID(room),
                    continuation_id=turn,
                    agent_handle=seat,
                    author="alice",
                    started_at=datetime.now(UTC),
                )
            )
            await db.commit()

    client.portal.call(seed)


def test_a_click_answers_the_message_and_its_replies_are_no_longer_offered(client):
    project_id, room, seat = _channel(client)
    _alice_is_answered(client, room, seat)

    asked = _say(
        client,
        project_id,
        room,
        seat,
        {
            "content": "报名的人要填什么？我建议照去年四项。",
            "replies": ["照去年四项", "只要姓名和学号"],
        },
    )
    assert asked.status_code == 200, asked.text
    question = _message(client, room, asked.json()["data"]["id"])
    assert [o["text"] for o in question["meta"]["options"]] == [
        "照去年四项",
        "只要姓名和学号",
    ]
    assert question["meta"]["asked"] == ["alice"]

    answer = post_message(
        client, room, "alice", {"content": "照去年四项", "reply_to": question["id"]}
    )

    assert answer["author"] == "alice"
    [entry] = _message(client, room, question["id"])["meta"]["answer_log"]
    assert (entry["by"], entry["option"]) == ("alice", "照去年四项")


def test_replies_are_offered_only_in_the_main_line(client):
    project_id, room, seat = _channel(client)
    task = open_task(client, room, "首页加报名表", owner="alice")

    refused = _say(
        client,
        project_id,
        task["id"],
        seat,
        {"content": "要哪一种？", "replies": ["甲", "乙"]},
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "chatRepliesMainLineOnly"
