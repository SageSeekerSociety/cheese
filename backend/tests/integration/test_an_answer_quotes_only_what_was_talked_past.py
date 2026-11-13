"""In a channel's main line, 芝士's answer quotes the message it answers only
when somebody else spoke between the question and the answer.

The rules, as a person would state them:

- Nobody spoke in between: the answer quotes nothing.
- 芝士's second message in the same turn quotes nothing either: its own first
  message is not somebody else talking.
- Somebody else spoke in between: the answer quotes the question.

The messages go the way a running turn sends them (`chat_send`, the agent's
own credential, while its turn is live).
"""

import time
import uuid

from tests.conftest import wait_work_idle
from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _channel(client, *others: str) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    for handle in others:
        join_project_team(client, project["id"], handle)
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    for handle in others:
        added = client.post(
            f"/topics/{room}/members",
            json={"handle": handle, "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert added.status_code == 200, added.text
    return project["id"], room


def _turn_held_open(stub_hooks) -> list[uuid.UUID]:
    """The session takes each input and keeps working: the turn stays live
    until the test stops it. Returns the rooms it started in."""
    started: list[uuid.UUID] = []

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic, agent=agent)
        stub_hooks.acknowledges(topic, prompt, agent=agent)
        started.append(topic)

    stub_hooks.emit_turn = turn
    return started


def _until(check, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _send(client, project: str, room: str, content: str) -> dict:
    """What 芝士 says with `chat_send` while its turn runs."""
    token = mint_scoped_token(
        project_id=project, topic_id=room, agent_handle=room_agent_seat(client, room)
    )
    bearer = client.headers.pop("Authorization", None)
    try:
        sent = client.post(
            f"/topics/{room}/messages",
            json={"request_id": str(uuid.uuid4()), "content": content},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer
    assert sent.status_code == 200, sent.text
    return sent.json()["data"]


def test_an_answer_nobody_talked_past_quotes_nothing(client, stub_hooks):
    project, room = _channel(client)
    started = _turn_held_open(stub_hooks)
    post_message(client, room, "alice", {"content": "首页要改哪些地方？"})
    _until(lambda: started)

    first = _send(client, project, room, "我看一眼就告诉你。")
    second = _send(client, project, room, "要改标题和配色。")
    stub_hooks.stops(started[0], "要改标题和配色。")
    wait_work_idle()

    assert first["reply_to"] is None
    assert second["reply_to"] is None


def test_an_answer_somebody_talked_past_quotes_the_question(client, stub_hooks):
    project, room = _channel(client, "bob")
    started = _turn_held_open(stub_hooks)
    seat = room_agent_seat(client, room)
    asked = post_message(
        client, room, "alice", {"content": f"<@{seat}> 首页要改哪些地方？"}
    )
    _until(lambda: started)
    post_message(client, room, "bob", {"content": "周四的组会改在 B201。"})

    first = _send(client, project, room, "要改标题和配色。")
    second = _send(client, project, room, "配色我先出两套。")
    stub_hooks.stops(started[0], "配色我先出两套。")
    wait_work_idle()

    assert first["reply_to"] == asked["id"]
    assert second["reply_to"] is None
