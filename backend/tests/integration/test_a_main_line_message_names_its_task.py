"""An AI teammate's message in the main line can say which task it is about.

The timeline then draws that task's tag above the message, and the page that
reads the channel gets the task with the message, so the tag can say how the
task stands. A task the channel does not have is refused with what to use
instead, rather than tagging the message with nothing.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, {"name": "Tags"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    return project["id"], room, room_agent_seat(client, room)


def _say(client, project_id: str, room: str, seat: str, task: str):
    token = mint_scoped_token(
        project_id=project_id, topic_id=room, agent_handle=seat, access_scope="project"
    )
    return client.post(
        f"/topics/{room}/messages",
        json={"content": "报名表做好了", "request_id": str(uuid.uuid4()), "task": task},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_a_message_about_a_task_carries_that_task(client):
    project_id, room, seat = _channel(client)
    task = open_task(client, room, "首页加报名表", owner="alice")

    said = _say(client, project_id, room, seat, f"#{task['number']}")
    assert said.status_code == 200, said.text
    message = said.json()["data"]

    page = client.get(f"/topics/{room}/blocks", headers=session_auth_headers("alice"))
    row = next(b for b in page.json()["data"]["data"] if b["id"] == message["id"])
    assert [t["id"] for t in row["tasks"]] == [task["id"]]


def test_a_task_the_channel_does_not_have_is_refused(client):
    project_id, room, seat = _channel(client)

    refused = _say(client, project_id, room, seat, "#999")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "chatTaskUnknown"
