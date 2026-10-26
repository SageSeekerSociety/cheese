"""The line under a main-line message that has a 支线: what someone reading the
main line is told without opening it.

- who took part, the message's author first;
- once the 支线 became a task, the same line: it is the same conversation,
  now with an owner, and what is said there goes on counting;
- a message whose 支线 has nothing in it yet and nobody answering has no line;
- a 支线 where the AI teammate's turn failed before anyone replied still has a
  line, saying the reply failed: the reason is in the 支线, and without the line
  nobody could find it;
- a page of 支线 reads the messages they hang under once, however many there
  are: who took part comes from the same rows as what the message said;
- a room's header asks three questions about its 支线 — the page's own list,
  回复了我, 还在当支线用 — and none of them is asked twice.
"""

import re
import uuid

import pytest
from sqlalchemy import event

from app.domain.block.models import Block, BlockKind
from app.domain.event_author import AuthorType
from app.domain.thread import reads as thread_reads
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


def test_a_thread_that_became_the_task_keeps_its_line(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "bob")
    post_message(client, thread, "bob", {"content": "我这边也是"})
    r = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    post_message(client, thread, "alice", {"content": "我来看看"})

    line = _line_under(client, tid, root["id"])
    assert line is not None and line["id"] == thread
    assert line["reply_count"] >= 2


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
    rows = _listed_threads(listed)
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
    rows = _listed_threads(listed)
    roots = {r["root_block_id"]: r["root"] for r in rows}

    assert roots[short["id"]]["truncated"] is False
    assert roots[short["id"]]["content"] == "微信里打不开预览"
    assert roots[short["id"]]["kind"] == "message"
    assert roots[short["id"]]["author_type"] == "participant"
    assert roots[long["id"]]["truncated"] is True


#: Any read of messages by their ids — one array, an IN list, or one id at a
#: time: a page that read its roots again in another spelling still counts.
_ROOTS_BY_ID = re.compile(r"FROM blocks\s+WHERE blocks\.id\b", re.IGNORECASE)


def _count_matching(client, read, pattern):
    """How many of the statements ``read`` runs ``pattern`` matches."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        read()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return sum(1 for s in statements if pattern.search(s))


def _roots_read_while(client, read):
    """How many times the messages are read by id while ``read`` runs."""
    return _count_matching(client, read, _ROOTS_BY_ID)


def _channel_with_threads(client, n):
    tid = _channel(client)
    for i in range(n):
        root = post_message(client, tid, "alice", {"content": f"问题 {i}"})
        thread = _thread(client, root["id"], "bob")
        post_message(client, thread, "bob", {"content": "我这边也是"})
    return tid


def _listed_threads(listed) -> list[dict]:
    """这一格画出来的那些支线。

    `GET /topics/{id}/threads` 现在分两栏（`thread/reads.in_room`：开着的和收起来的），
    这里要的是开着的那些；`data` 那一层是早先形状的回退，读同一个接口的老测试还在。
    """
    rows = listed["data"] if isinstance(listed, dict) and "data" in listed else listed
    if isinstance(rows, dict):
        return rows["open"]
    return rows


def _list_threads(client, tid):
    r = client.get(f"/topics/{tid}/threads", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return _listed_threads(r.json()["data"])


@pytest.mark.parametrize("n", [3, 12])
def test_a_page_of_threads_reads_their_messages_once(client, n):
    tid = _channel_with_threads(client, n)
    rows: list[dict] = []
    reads = _roots_read_while(client, lambda: rows.extend(_list_threads(client, tid)))
    assert len(rows) == n
    assert all(r["participants"] == ["alice", "bob"] for r in rows)
    assert reads == 1, f"{n} 条支线读了 {reads} 遍根消息"


def test_the_people_in_a_thread_still_read_its_message_alone(client):
    tid = _channel(client)
    root = post_message(client, tid, "alice", {"content": "微信里打不开预览"})
    thread = _thread(client, root["id"], "bob")
    post_message(client, thread, "bob", {"content": "我这边也是"})

    async def _people() -> list[str]:
        async with client.test_factory() as s:
            return await thread_reads.people_in(s, uuid.UUID(thread))

    assert client.portal.call(_people) == ["alice", "bob"]


#: Any read of a room's 支线 read straight from the table, whichever question it
#: answers — the page's own list, 回复了我, 还在当支线用 — and however many rows
#: it takes. A 支线 read by its id is a different question and not counted.
_THREADS_OF_A_ROOM = re.compile(
    r"^\s*SELECT threads\.[^;]*?\bFROM threads\s+WHERE threads\.room_id",
    re.IGNORECASE,
)


def _room_header(client, tid):
    r = client.get(f"/topics/{tid}", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.parametrize("n", [3, 12])
def test_a_rooms_header_reads_its_threads_no_more_than_three_times(client, n):
    """A room's header asks three separate questions about its 支线 — the page's
    own list, 回复了我, 还在当支线用 — and asks each once, however many 支线 the
    room has.

    This is one route's whole budget: a new question of the header that reads the room's 支线 itself is meant to
    be shared with the rest, and this ceiling is where that gets noticed. A
    ceiling rather than the exact three, so merging two of the remaining
    questions into one query reads as the improvement it is."""
    tid = _channel_with_threads(client, n)

    reads = _count_matching(
        client, lambda: _room_header(client, tid), _THREADS_OF_A_ROOM
    )

    # Both ends: a spelling of the query this pattern does not know would read
    # as zero and pass the ceiling. The floor only catches that total silence —
    # a read rewritten in a spelling the pattern skips still hides an extra
    # read without making the count reach zero.
    assert 1 <= reads <= 3, f"{n} 条支线时这个房间的头读了 {reads} 遍支线表"
