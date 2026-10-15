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
from app.domain.usage.credits import CREDITS_EXHAUSTED_META, credits_event
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
    assert line["tasks"] == []


def test_the_line_names_the_tasks_made_from_the_thread(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "预览有两个问题"})
    thread = _thread(client, root["id"], "bob")
    said = post_message(client, thread, "bob", {"content": "微信里打不开预览"})
    r = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text

    line = _line_under(client, tid, root["id"])

    assert [t["id"] for t in line["tasks"]] == [r.json()["data"]["id"]]


def test_a_thread_that_became_the_task_has_no_line(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "bob")
    post_message(client, thread, "bob", {"content": "我这边也是"})
    r = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text

    assert _line_under(client, tid, root["id"]) is None


def test_an_empty_thread_with_nobody_answering_has_no_line(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    _thread(client, root["id"], "bob")

    assert _line_under(client, tid, root["id"]) is None


def _turn_failed(client, thread_id, content=None, meta=None):
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
                    content=content or "本轮未完成：这条会话的机器尚未配置或未连接",
                    meta=meta or {"event_type": "turn_failed", "severity": "error"},
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


def test_a_checklist_posted_in_a_thread_tells_the_channel(client, monkeypatch):
    """A checklist posted into a 支线 is one of its replies (the line under the
    message counts it), so the channel is told the 支线 grew, as it is for a
    plain reply. Without that the line under the message keeps the count it
    was read with: it said 2 条回复 while the 支线 itself held 3."""
    import app.domain.agent.staleness as staleness

    heard: list[tuple[str, str]] = []

    async def announce(conversation_id, resource, *, id=None):
        heard.append((str(conversation_id), resource))

    monkeypatch.setattr(staleness, "announce_stale", announce)
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "照着赛题要求列出要做的几步"})
    thread = _thread(client, root["id"], "alice")
    post_message(client, thread, "alice", {"content": "先别写代码"})
    heard.clear()

    r = client.put(
        f"/topics/{thread}/progress",
        json={"todos": [{"content": "读赛题", "status": "pending"}]},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert (tid, "threads") in heard
    assert _line_under(client, tid, root["id"])["reply_count"] == 2


def test_a_thread_the_credits_refused_says_so_on_the_main_line(client):
    """Spent credits are not a failure anyone can retry: the line under the
    message carries why, so the channel can say the credits ran out."""
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "把周报改一版"})
    thread = _thread(client, root["id"], "alice")
    _turn_failed(
        client,
        thread,
        content=credits_event("本月额度已用完，11月1日重置。"),
        meta=CREDITS_EXHAUSTED_META,
    )

    line = _line_under(client, tid, root["id"])

    assert line["failed"] is True
    assert line["failed_code"] == "credits_exhausted"


def _said(client, thread_id, author, content, meta):
    """A message written straight into a 支线, the way an older version of the
    platform wrote it."""

    async def _write() -> None:
        async with client.test_factory() as s:
            thread = await s.get(Thread, uuid.UUID(thread_id))
            assert thread is not None
            s.add(
                Block(
                    project_id=thread.project_id,
                    conversation_id=thread.id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author=author,
                    content=content,
                    meta=meta,
                )
            )
            await s.commit()

    client.portal.call(_write)


def test_an_old_checklist_reply_is_still_known_as_a_checklist(client):
    """Checklists written before their steps were stored as a structure carry
    only a mark that they are one; their text is one marked step per line. The
    line under the message has to be told, or it shows the steps folded into a
    row of marks."""
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "照着赛题要求列出要做的几步"})
    thread = _thread(client, root["id"], "alice")
    _said(client, thread, "cheese", "✓ 读赛题\n✱ 列步骤\n○ 回结论", {"checklist": True})

    line = _line_under(client, tid, root["id"])

    assert line["last_reply"]["checklist"] is True


def test_a_listed_thread_says_whether_its_message_was_cut(client):
    """The list of a channel's 支线 carries a preview of each opening message.
    Whoever draws the message itself from it must know when the preview is
    not all of it."""
    tid = _channel(client)
    short = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    long = post_message(client, tid, "alice", {"content": "长" * 500})
    for root in (short, long):
        post_message(
            client, _thread(client, root["id"], "bob"), "bob", {"content": "收到"}
        )

    listed = client.get(
        f"/topics/{tid}/threads", headers=session_auth_headers("alice")
    ).json()["data"]
    rows = listed["data"] if isinstance(listed, dict) else listed
    roots = {r["root_block_id"]: r["root"] for r in rows}

    assert roots[short["id"]]["truncated"] is False
    assert roots[short["id"]]["content"] == "微信里打不开预览"
    assert roots[short["id"]]["kind"] == "message"
    assert roots[short["id"]]["author_type"] == "participant"
    assert roots[long["id"]]["truncated"] is True
