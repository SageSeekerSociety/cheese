"""A comment on a room file that names the room's agent is answered in its
thread by the thread's own session, the way a document's comment is.

- The answer is the agent's reply in the thread, under the agent's name.
- The session is told the file, read as text, and where the comment points.
- The thread list says the answer is on its way (``answering``), because a
  file's thread has no collaboration connection to hear it on.
- A comment that names nobody, or only a person, asks nothing.
- A file the room does not hold is not a thread at all.
- The answer's tools read only what everyone who reads the room may.

The session host is faked at its boundary (``SessionHost``), the same stand-in
`test_doc_agent.py` uses; the collaboration service is
tests/support/collab.py.
"""

import asyncio
import time
import uuid

import httpx
import pytest

from app.api.deps import (
    consumptions_for,
    get_consumptions,
    get_session_host,
)
from app.auth.project_access import may_read_project, outsiders_reading
from app.domain.library import service as library
from app.domain.project.models import Project
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.repositories import UserRepository
from app.main import app
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_message,
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


def _comment(
    client, room: str, content: str, *, by: str = "alice", path: str = PATH
) -> str:
    response = client.post(
        _comments(room),
        json={
            "path": path,
            "content": content,
            # 读者指着一处说的一句话：面板把「哪一页」和「页面上的哪一点」拼在一起
            # 存进 anchor_quote（见 PanelPreviewView 的 commentPick）。
            "quote": "第 2 页 · 从左 40%、从上 60%",
            "anchor": {
                "kind": "page-pin",
                "path": path,
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


def _thread(client, room: str, root: str, *, path: str = PATH) -> dict:
    return client.get(
        f"{_comments(room)}/{root}/thread",
        params={"path": path},
        headers=session_auth_headers("alice"),
    ).json()["data"]


def _answers(
    client, room: str, root: str, seat: str, count: int = 1, *, path: str = PATH
) -> list[str]:
    """The agent's replies in the thread, once there are ``count`` of them."""
    deadline = time.monotonic() + 20
    while True:
        replies = [
            r["comment"]["content"]
            for r in _thread(client, room, root, path=path)["replies"]
            if r["comment"]["author"] == seat
        ]
        if len(replies) >= count or time.monotonic() > deadline:
            return replies
        time.sleep(0.1)


def test_a_pdf_is_read_as_its_text_so_it_can_answer_about_a_page(client, sessions):
    """A delivered report is usually a PDF, and the comment points at a page of
    it: the session has to read its text layer, not "binary file, 12 KB"."""
    import fitz

    room, project, seat = _room_with_file(client)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 96), "completion this quarter: 42 percent", fontname="helv")
    library.write_room_file(
        uuid.UUID(project),
        uuid.UUID(room),
        "output/报告.pdf",
        pdf.tobytes(),
    )
    pdf.close()

    root = _comment(
        client,
        room,
        f"<@{seat}> is the number on this page right?",
        path="output/报告.pdf",
    )

    assert _answers(client, room, root, seat, path="output/报告.pdf") == ["好的。"]
    [(_, question)] = sessions.asked
    assert "completion this quarter: 42 percent" in question


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


def test_the_answer_reads_only_what_every_reader_of_the_room_may(client, sessions):
    """A file's answer is read by everyone who reads the room, so its tools read
    only what all of them may: the project's public channels are not carried
    into a room somebody outside the project reads.

    Somebody outside gets onto a private channel's roster like this: on the
    team, seated on the channel, then removed from the team. Removing somebody
    from the team soft-deletes that relation and nothing else, so the channel
    seat stays while the project goes — ``outsiders_reading`` then names them,
    and they still read the file's threads."""
    for who in ("dave", "bob"):
        seed_user(client, who)
    project = post_project(client, json={"name": "文件受众"}, owner="alice").json()[
        "data"
    ]["id"]
    join_project_team(client, project, "bob")
    public = client.post(
        "/topics",
        json={"project_id": project, "title": "公开"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    post_message(client, public, "alice", {"content": "紫色长颈鹿的预算"})

    room = client.post(
        "/topics",
        json={"project_id": project, "title": "机密", "members_only": True},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    seated = client.post(
        f"/topics/{room}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert seated.status_code == 200, seated.text
    library.write_room_file(uuid.UUID(project), uuid.UUID(room), PATH, REPORT.encode())
    seat = room_agent_seat(client, room)

    # Off the team, seat kept: what DELETE /teams/{id}/members/{uid} does.
    async def off_the_team():
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            p = await db.get(Project, uuid.UUID(project))
            bob = await UserRepository(db).get_by_handle("bob")
            await TeamService(TeamRepository(db)).remove_team_member(
                team_id=p.team_id, target_user_id=bob.id
            )
            await db.commit()

    asyncio.run(off_the_team())

    async def reading():
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            return (
                await may_read_project(db, project_id=uuid.UUID(project), handle="bob"),
                await outsiders_reading(
                    db, project_id=uuid.UUID(project), room_id=uuid.UUID(room)
                ),
            )

    reads_project, outside = asyncio.run(reading())
    assert not reads_project and outside == ["bob"], "the outsider shape was not built"
    # He is still read: the thread is what his answer reaches.
    assert (
        client.get(
            f"{_comments(room)}/threads",
            params={"path": PATH},
            headers=session_auth_headers("bob"),
        ).status_code
        == 200
    )

    seen: dict = {}

    async def look(credential, question):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform"
        ) as page:
            found = await page.get(
                f"/projects/{project}/context/search",
                params={"q": "紫色长颈鹿的预算"},
                headers={"X-Cheese-Token": credential},
            )
        seen["status"] = found.status_code
        seen["rooms"] = (
            {hit["room_id"] for hit in found.json()["data"]["hits"]["records"]}
            if found.status_code == 200
            else set()
        )
        return "好的。", None

    sessions.script = look
    root = _comment(client, room, f"<@{seat}> 这里的预算按谁的算？")

    assert _answers(client, room, root, seat) == ["好的。"]
    assert seen["status"] == 200, seen
    assert public not in seen["rooms"], "the public channel was read into that answer"


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
