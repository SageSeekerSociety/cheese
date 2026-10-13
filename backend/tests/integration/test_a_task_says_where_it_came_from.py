"""A task's page says which message it came from, and where to find it.

The rules, stated before the code:

- a task turned from a message quotes that message, and names the
  conversation it is in, so the page can open it there: the main line, or the
  支线 when it was a reply in one;
- a task an AI teammate creates quotes the person's message that asked for it:
  in a 支线, the latest thing a person said there; in the main line, the latest
  message addressed to that teammate, never someone talking to someone else;
- a task nobody made from a message quotes nothing.

On dev (2026-10-11) people opened tasks and could not tell what they were for:
the page said where a task came from only at the very bottom, under the whole
document, and a task a teammate made in the main line said nothing at all.
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import (
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

ALICE = session_auth_headers("alice")


def _channel(client) -> tuple[str, str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics", json={"project_id": project["id"], "title": "修复"}, headers=ALICE
    ).json()["data"]["id"]
    return project["id"], channel, room_agent_seat(client, channel)


def _thread(client, block_id: str) -> str:
    opened = client.post(f"/blocks/{block_id}/thread", headers=ALICE)
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["id"]


def _source(client, task: str) -> dict | None:
    r = client.get(f"/topics/{task}/related", headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["data"]["source"]


def _teammate_task(client, project: str, conversation: str, seat: str) -> str:
    made = client.post(
        f"/topics/{conversation}/teammate-tasks",
        json={"title": "修贴底", "summary": "回复变长时对话栏不再贴底"},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=project, topic_id=conversation, agent_handle=seat
            )
        },
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["id"]


def test_a_message_turned_into_a_task_is_quoted_where_it_was_said(client):
    _project, channel, _seat = _channel(client)
    said = post_message(client, channel, "alice", {"content": "设置页的保存按钮没反应"})

    task = client.post(f"/blocks/{said['id']}/upgrade", headers=ALICE).json()["data"]

    source = _source(client, task["id"])
    assert source["block_id"] == said["id"]
    assert source["author"] == "alice"
    assert source["content"] == "设置页的保存按钮没反应"
    assert (source["where"], source["conversation_id"]) == ("channel", channel)
    assert source["title"] == "修复"


def test_a_reply_in_a_thread_turned_into_a_task_is_quoted_in_that_thread(client):
    _project, channel, _seat = _channel(client)
    root = post_message(client, channel, "alice", {"content": "对话栏有问题"})
    thread = _thread(client, root["id"])
    reply = post_message(client, thread, "alice", {"content": "回复变长时不贴底了"})

    task = client.post(f"/blocks/{reply['id']}/upgrade", headers=ALICE).json()["data"]

    source = _source(client, task["id"])
    assert source["block_id"] == reply["id"]
    assert source["content"] == "回复变长时不贴底了"
    assert (source["where"], source["conversation_id"]) == ("thread", thread)


def test_a_teammates_task_in_a_thread_quotes_what_the_person_said_there(client):
    project, channel, seat = _channel(client)
    root = post_message(client, channel, "alice", {"content": "对话栏有问题"})
    thread = _thread(client, root["id"])
    asked = post_message(
        client, thread, "alice", {"content": "经常停在中间，开个任务修一下"}
    )

    task = _teammate_task(client, project, thread, seat)

    source = _source(client, task)
    assert source["block_id"] == asked["id"]
    assert source["where"] == "thread"


def test_a_teammates_task_in_the_main_line_quotes_the_message_that_asked_it(client):
    project, channel, seat = _channel(client)
    asked = post_message(
        client, channel, "alice", {"content": f"<@{seat}> 把拖拽排序修好"}
    )
    post_message(client, channel, "alice", {"content": "今天谁值班？"})

    task = _teammate_task(client, project, channel, seat)

    source = _source(client, task)
    assert source["block_id"] == asked["id"]
    assert source["where"] == "channel"


def test_a_teammates_task_quotes_nothing_when_no_one_asked_it(client):
    project, channel, seat = _channel(client)
    post_message(client, channel, "alice", {"content": "今天谁值班？"})

    task = _teammate_task(client, project, channel, seat)

    assert _source(client, task) is None


def test_a_task_someone_created_on_its_own_quotes_nothing(client):
    _project, channel, _seat = _channel(client)
    task = client.post(
        f"/topics/{channel}/tasks", json={"title": "整理日志"}, headers=ALICE
    ).json()["data"]["id"]

    assert _source(client, task) is None
