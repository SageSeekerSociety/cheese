"""An AI teammate speaks in, and closes, a task only from that task's own session.

The same agent works a channel and each of its tasks in separate sessions. A
session of the channel that tries to write into one of its tasks is refused,
and the refusal has to say why: it used to say that only the task's owner may
do this, which sent the agent looking for an owner's permission it could never
get, when the task's own session may do exactly the same thing.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.channel import mint_session_token
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)


def _channel_and_task(client) -> tuple[str, str, str, str]:
    project = post_project(client, {"name": "Sessions"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = open_task(client, room, "查一个问题", owner="alice")
    return project["id"], room, task["id"], seat


def _channel_session(project_id: str, room: str, seat: str) -> dict[str, str]:
    """The credential the agent's session of the channel presents: minted in
    the channel and reaching across its project, as a session's does."""
    token = mint_scoped_token(
        project_id=project_id, topic_id=room, agent_handle=seat, access_scope="project"
    )
    return {"Authorization": f"Bearer {token}"}


def _say(client, task_id: str, headers: dict[str, str]):
    return client.post(
        f"/topics/{task_id}/messages",
        json={"content": "这条线索给任务。", "request_id": str(uuid.uuid4())},
        headers=headers,
    )


def test_the_channel_session_is_told_to_speak_from_the_task_s_own_session(client):
    project_id, room, task_id, seat = _channel_and_task(client)

    refused = _say(client, task_id, _channel_session(project_id, room, seat))
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionSpeaks"

    # The same agent, from the task's own session, is heard.
    heard = _say(client, task_id, task_agent_headers(project_id, task_id, seat))
    assert heard.status_code == 200, heard.text
    assert heard.json()["data"]["author"] == seat


def test_the_channel_session_is_told_who_may_close_a_task(client):
    project_id, room, task_id, seat = _channel_and_task(client)

    refused = client.post(
        f"/topics/{task_id}/close",
        json={},
        headers=_channel_session(project_id, room, seat),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskOwnSessionCloses"

    task = client.get(f"/topics/{task_id}/task", headers=session_auth_headers("alice"))
    assert task.status_code == 200, task.text
    assert task.json()["data"]["status"] != "closed"


def test_a_task_session_reads_a_neighbouring_task_through_the_channel(client):
    """一条任务的会话读不到旁边那条任务，但那句拒绝得指路。

    它以前只说「这段凭据属于另一个对话」，停在墙前的人只能去问负责人。按整个
    频道读是通的，所以这句话要说出来，并且那条路真的通。"""
    project_id, room, task_id, seat = _channel_and_task(client)
    other = open_task(client, room, "另一件事", owner="alice")["id"]
    said = client.post(
        f"/topics/{other}/messages",
        json={"content": "另一条任务里说过的话", "request_id": str(uuid.uuid4())},
        headers=session_auth_headers("alice"),
    )
    assert said.status_code == 200, said.text
    headers = {
        "Authorization": f"Bearer {mint_session_token(project_id, task_id, seat)}"
    }

    refused = client.get(f"/topics/{other}/history", headers=headers)

    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert error["i18n"]["key"] == "taskCredentialOtherConversation"
    assert "channel" in error["message"], error["message"]

    # 它指的那条路真的通：同一个会话按整个频道读得到旁边那条任务的话。
    read = client.get(f"/topics/{task_id}/history?channel=true", headers=headers)
    assert read.status_code == 200, read.text
    assert "另一条任务里说过的话" in [b["content"] for b in read.json()["data"]["data"]]
