"""The line under a main-line message that has a 支线: what someone reading the
main line is told without opening it.

- who took part, the message's author first;
- once the 支线 became a task, which task it is — what was said there goes on
  in the task;
- a message whose 支线 has nothing in it yet and nobody answering has no line;
- a 支线 where the AI teammate's turn failed before anyone replied still has a
  line, saying the reply failed: the reason is in the 支线, and without the line
  nobody could find it.
"""

import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.thread.models import Thread
from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _channel(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _line_under(client, tid, block_id):
    blocks = client.get(
        f"/topics/{tid}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(b for b in blocks if b["id"] == block_id).get("thread")


def _thread(client, block_id, who):
    r = client.post(f"/blocks/{block_id}/thread", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_the_line_names_who_took_part(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "bob")
    post_message(client, thread, "bob", {"content": "我这边也是"})

    line = _line_under(client, tid, root["id"])

    assert line["reply_count"] == 1
    assert line["participants"] == ["alice", "bob"]
    assert line["task"] is None


def test_the_line_names_the_task_the_thread_became(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "bob")
    post_message(client, thread, "bob", {"content": "我这边也是"})
    r = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text

    line = _line_under(client, tid, root["id"])

    assert line["task"]["id"] == r.json()["data"]["id"]


def test_an_empty_thread_with_nobody_answering_has_no_line(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    _thread(client, root["id"], "bob")

    assert _line_under(client, tid, root["id"]) is None


def _turn_failed(client, thread_id):
    async def _write() -> None:
        async with client.test_factory() as s:
            thread = await s.get(Thread, uuid.UUID(thread_id))
            assert thread is not None
            s.add(
                Block(
                    project_id=thread.project_id,
                    conversation_id=thread.id,
                    kind=BlockKind.event,
                    author_type=AuthorType.platform,
                    author="system",
                    content="本轮未完成：这条会话的机器尚未配置或未连接",
                    meta={"event_type": "turn_failed", "severity": "error"},
                )
            )
            await s.commit()

    client.portal.call(_write)


def test_a_thread_whose_turn_failed_before_any_reply_still_has_a_line(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "alice")
    _turn_failed(client, thread)

    line = _line_under(client, tid, root["id"])

    assert line is not None
    assert line["reply_count"] == 0
    assert line["failed"] is True
    listed = client.get(
        f"/topics/{tid}/threads", headers=session_auth_headers("alice")
    ).json()["data"]
    rows = listed["data"] if isinstance(listed, dict) else listed
    assert [r["id"] for r in rows if r["failed"]] == [thread]


def test_a_reply_after_the_failure_clears_it(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "alice")
    _turn_failed(client, thread)
    post_message(client, thread, "bob", {"content": "我这边也是"})

    line = _line_under(client, tid, root["id"])

    assert line["reply_count"] == 1
    assert line["failed"] is False
