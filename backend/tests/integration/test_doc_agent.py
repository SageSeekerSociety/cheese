"""A document comment that names the room's agent is answered in its thread by
the thread's own session, not by a turn of the room.

- The agent's answer is its reply in the thread, under the agent's name.
- One thread is one session: a later question in the thread goes to the same one.
- What the session changes in the document is recorded as done by the agent
  for the person who asked.
- Its tools act with the credential minted for the question: they read what the
  asker may read, and stop working once the answer is over.
- A session that fails still leaves the thread an answer from the agent.

The session itself is faked at its boundary (``HandlessSessions.ask``); its
tools reach the platform the way a real session's do, as the request each table
tool plans (`sandbox/cheese`), with the question's credential. The
collaboration service is tests/support/collab.py.
"""

import base64
import json
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.api.deps import get_handless_sessions
from app.domain.agent.document import question as doc_question
from app.domain.agent.harness.pi import catalog
from app.domain.agent.harness.pi.handless import Answered
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.main import app
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_doc_edits import ALICE_PARAGRAPH, _doc, _document


class FakeSessions:
    """The session host: every question it is asked, answered by ``script``."""

    def __init__(self) -> None:
        self.asked: list[tuple] = []
        self.script: Callable[..., Awaitable[tuple[str, str | None]]] | None = None

    async def ask(self, launch, work_id, text, *, credential, earlier="", ceiling_s):
        self.asked.append((launch, text))
        answer, error = "好的。", None
        if self.script is not None:
            answer, error = await self.script(credential, text)
        yield Answered(answer, error)


@pytest.fixture
def sessions():
    fake = FakeSessions()
    app.dependency_overrides[get_handless_sessions] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_handless_sessions, None)


async def _tool(credential: str, name: str, args: dict) -> httpx.Response:
    """A tool call as the session makes it: the request the table plans for it,
    in the room the session answers in, with the question's credential."""
    # Where the session answers, read off the credential without judging it:
    # the platform is what judges it.
    body = credential.removeprefix("cxdg_").split(".", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    plan = catalog._module().request_plan(
        name, args, {"CHEESE_PROJECT": claims["p"], "CHEESE_TOPIC": claims["t"]}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform"
    ) as platform:
        return await platform.request(
            plan["method"],
            plan["path"],
            json=plan.get("body"),
            headers={"X-Cheese-Token": credential},
        )


def _comment(client, room: str, content: str, *, by: str = "alice") -> str:
    response = client.post(
        f"/topics/{room}/comments",
        json={"content": content, "quote": "范围"},
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _thread(client, room: str, root: str) -> dict:
    return client.get(
        f"/topics/{room}/comments/{root}/thread", headers=session_auth_headers("alice")
    ).json()["data"]


def _reply(client, room: str, root: str, content: str, *, by: str = "alice"):
    response = client.post(
        f"/topics/{room}/comments/{root}/replies",
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_revision": _thread(client, room, root)["revision"],
            "content": content,
        },
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text


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
    room, seat = _document(client)

    root = _comment(client, room, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, room, root, seat) == ["好的。"]
    [(launch, question)] = sessions.asked
    assert launch.key == uuid.UUID(root)
    assert "这里的范围指什么？" in question
    assert ALICE_PARAGRAPH in question


def test_the_thread_list_says_while_the_agent_is_answering(client, sessions):
    room, seat = _document(client)
    seen: list = []

    async def look(credential, question):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform"
        ) as page:
            listed = await page.get(
                f"/topics/{room}/comments/threads",
                headers=session_auth_headers("alice"),
            )
        seen.extend(t["answering"] for t in listed.json()["data"]["data"])
        return "好的。", None

    sessions.script = look
    root = _comment(client, room, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, room, root, seat) == ["好的。"]
    assert seen == ["working"]
    listed = client.get(
        f"/topics/{room}/comments/threads", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    assert [t["answering"] for t in listed] == [None]


def test_a_comment_naming_nobody_or_a_person_asks_nothing(client, sessions):
    room, seat = _document(client)

    root = _comment(client, room, "<@bob> 回头自己再看")
    _comment(client, room, "范围要再定一下")
    time.sleep(1)

    assert sessions.asked == []
    assert _thread(client, room, root)["replies"] == []


def test_a_later_question_in_the_thread_goes_to_the_same_session(client, sessions):
    room, seat = _document(client)
    root = _comment(client, room, "这里要不要展开")

    _reply(client, room, root, f"<@{seat}> 你来看看", by="bob")

    assert _answers(client, room, root, seat) == ["好的。"]
    _reply(client, room, root, f"<@{seat}> 再短一点")
    assert len(_answers(client, room, root, seat, count=2)) == 2
    first, second = sessions.asked
    assert first[0].key == second[0].key == uuid.UUID(root)
    assert "这里要不要展开" in first[1] and "你来看看" in first[1]


def test_what_the_session_changes_is_recorded_as_asked_by_the_commenter(
    client, sessions
):
    room, seat = _document(client)

    async def edit(credential, question):
        response = await _tool(
            credential,
            "cheese_doc_edit",
            {"edits": [{"old": "讲范围", "new": "讲边界"}]},
        )
        assert response.status_code == 200, response.text
        return "改好了。", None

    sessions.script = edit
    root = _comment(client, room, f"<@{seat}> 把范围改成边界", by="bob")

    assert _answers(client, room, root, seat) == ["改好了。"]
    assert "李老师写的第二段，讲边界。" in _doc(client, room)["content"]
    latest = client.get(
        f"/topics/{room}/doc/history", headers=session_auth_headers("alice")
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == seat and latest["requested_by"] == "bob"


def test_the_tools_stop_working_once_the_answer_is_over(client, sessions, monkeypatch):
    room, seat = _document(client)
    monkeypatch.setattr(doc_question, "ANSWER_S", 0.0)
    monkeypatch.setattr(doc_question, "CREDENTIAL_MARGIN_S", 1)
    held: dict[str, str] = {}

    async def keep(credential, question):
        held["credential"] = credential
        return "看过了。", None

    sessions.script = keep
    root = _comment(client, room, f"<@{seat}> 看一下")
    assert _answers(client, room, root, seat) == ["看过了。"]
    time.sleep(1.5)

    edit = client.portal.call(
        _tool,
        held["credential"],
        "cheese_doc_edit",
        {"edits": [{"old": "范围", "new": "x"}]},
    )
    assert edit.status_code == 401
    assert ALICE_PARAGRAPH in _doc(client, room)["content"]


def test_a_session_that_fails_still_answers_the_thread(client, sessions):
    room, seat = _document(client)

    async def fail(credential, question):
        return "", "the model call failed"

    sessions.script = fail
    root = _comment(client, room, f"<@{seat}> 这段对吗")

    [answer] = _answers(client, room, root, seat)
    assert answer


def test_a_search_reaches_only_the_rooms_the_asker_may_read(client, sessions):
    room, seat = _document(client)
    project = client.get(f"/topics/{room}").json()["data"]["project_id"]
    alone = client.get(
        f"/projects/{project}/private-chat",
        params={"user_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert alone.status_code == 200, alone.text
    post_message(client, alone.json()["data"]["id"], "alice", {"content": "里程碑七号"})
    post_message(client, room, "alice", {"content": "里程碑三号"})
    found: dict[str, str] = {}

    async def search(credential, question):
        response = await _tool(credential, "cheese_project_search", {"query": "里程碑"})
        assert response.status_code == 200, response.text
        found["text"] = response.text
        return "找到了。", None

    sessions.script = search
    root = _comment(client, room, f"<@{seat}> 里程碑定了几号？", by="bob")

    assert _answers(client, room, root, seat) == ["找到了。"]
    assert "三号" in found["text"]
    assert "七号" not in found["text"]


def test_the_team_memory_is_read_in_full(client, sessions):
    room, seat = _document(client)
    project = uuid.UUID(client.get(f"/topics/{room}").json()["data"]["project_id"])

    async def remember():
        async with client.test_factory() as db:
            await MemoryFileStore(db).write(
                project_id=project,
                scope=MemoryFileScope.team,
                owner_handle=None,
                path="deploy.md",
                content="部署走 CI，周五不发版。",
                updated_by="alice",
                expected_version=None,
            )
            await db.commit()

    client.portal.call(remember)
    read: dict[str, str] = {}

    async def recall(credential, question):
        response = await _tool(credential, "cheese_memory_read", {"name": "deploy.md"})
        assert response.status_code == 200, response.text
        read["text"] = next(
            row["content"]
            for row in response.json()["data"]["data"]
            if row["path"] == "deploy.md"
        )
        return "记得。", None

    sessions.script = recall
    root = _comment(client, room, f"<@{seat}> 什么时候能发版？", by="bob")

    assert _answers(client, room, root, seat) == ["记得。"]
    assert read["text"] == "部署走 CI，周五不发版。"
