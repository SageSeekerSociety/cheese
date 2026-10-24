"""A comment on a room file that names the room's agent is answered in its
thread by the thread's own session, the way a document's comment is.

- The answer is the agent's reply in the thread, under the agent's name.
- The session is told the file, read as text, and where the comment points.
- The thread list says the answer is on its way (``answering``), because a
  file's thread has no collaboration connection to hear it on.
- A comment that names nobody, or only a person, asks nothing.
- A file the room does not hold is not a thread at all.

The session host is faked at its boundary (``SessionHost``), the same stand-in
`test_doc_agent.py` uses; the collaboration service is
tests/support/collab.py.
"""

import time
import uuid

import httpx
import pytest

from app.api.deps import (
    consumptions_for,
    get_consumptions,
    get_session_host,
)
from app.domain.library import service as library
from app.main import app
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_doc_agent import FakeSessions, _chat

PATH = "output/季度报告.md"
REPORT = "第一段：范围。\n\n第二段：本季度完成率 42%。\n"


def _room_with_file(client) -> tuple[str, str, str]:
    """A room of alice's with one delivered file in it. Returns (room, project,
    the room's agent seat)."""
    project = post_project(client, json={"name": "文件评论"}, owner="alice").json()[
        "data"
    ]["id"]
    room = client.post(
        "/topics",
        json={"project_id": project, "title": "改一份报告"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    library.write_room_file(
        uuid.UUID(project), uuid.UUID(room), PATH, REPORT.encode("utf-8")
    )
    return room, project, room_agent_seat(client, room)


@pytest.fixture
def sessions():
    fake = FakeSessions()
    questions = consumptions_for(fake, _chat())  # type: ignore[arg-type]
    app.dependency_overrides[get_session_host] = lambda: fake
    app.dependency_overrides[get_consumptions] = lambda: questions
    yield fake
    app.dependency_overrides.pop(get_session_host, None)
    app.dependency_overrides.pop(get_consumptions, None)


def _comments(room: str) -> str:
    return f"/topics/{room}/files/comments"


def _comment(client, room: str, content: str, *, by: str = "alice") -> str:
    response = client.post(
        _comments(room),
        json={
            "path": PATH,
            "content": content,
            # 读者指着一处说的一句话：面板把「哪一页」和「页面上的哪一点」拼在一起
            # 存进 anchor_quote（见 PanelPreviewView 的 commentPick）。
            "quote": "第 2 页 · 从左 40%、从上 60%",
            "anchor": {
                "kind": "page-pin",
                "path": PATH,
                "source": "live",
                "version": "0123456789abcdef",
                "task_id": None,
                "page": 2,
                "x": 0.4,
                "y": 0.6,
            },
        },
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _thread(client, room: str, root: str) -> dict:
    return client.get(
        f"{_comments(room)}/{root}/thread",
        params={"path": PATH},
        headers=session_auth_headers("alice"),
    ).json()["data"]


def _answers(client, room: str, root: str, seat: str, count: int = 1) -> list[str]:
    """The agent's replies in the thread, once there are ``count`` of them."""
    deadline = time.monotonic() + 20
    while True:
        replies = [
            r["comment"]["content"]
            for r in _thread(client, room, root)["replies"]
            if r["comment"]["author"] == seat
        ]
        if len(replies) >= count or time.monotonic() > deadline:
            return replies
        time.sleep(0.1)


def test_a_comment_naming_the_agent_is_answered_in_its_thread(client, sessions):
    room, _, seat = _room_with_file(client)

    root = _comment(client, room, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, room, root, seat) == ["好的。"]
    [(session, question)] = sessions.asked
    # One thread, one session, keyed by the thread's opening comment.
    assert root in session.home
    assert "这里的范围指什么？" in question
    # The file is read as text, and the place the comment points at is named.
    assert "本季度完成率 42%" in question
    assert "第 2 页" in question


def test_the_thread_list_says_while_the_agent_is_answering(client, sessions):
    room, _, seat = _room_with_file(client)
    seen: list = []
    threads = f"{_comments(room)}/threads"

    async def look(credential, question):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform"
        ) as page:
            listed = await page.get(
                threads, params={"path": PATH}, headers=session_auth_headers("alice")
            )
        seen.extend(t["answering"] for t in listed.json()["data"]["data"])
        return "好的。", None

    sessions.script = look
    root = _comment(client, room, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, room, root, seat) == ["好的。"]
    assert seen == ["working"]
    listed = client.get(
        threads, params={"path": PATH}, headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [t["answering"] for t in listed] == [None]


def test_a_comment_naming_nobody_or_a_person_asks_nothing(client, sessions):
    room, _, seat = _room_with_file(client)

    _comment(client, room, "这一处再核对一下。")
    _comment(client, room, "<@bob> 你看呢？", by="alice")
    time.sleep(0.3)

    assert sessions.asked == []


def test_a_file_the_room_does_not_hold_is_not_a_thread(client, sessions):
    room, _, _ = _room_with_file(client)

    response = client.post(
        _comments(room),
        json={"path": "output/没有这份.md", "content": "喂"},
        headers=session_auth_headers("alice"),
    )

    assert response.status_code == 404, response.text
    assert sessions.asked == []
