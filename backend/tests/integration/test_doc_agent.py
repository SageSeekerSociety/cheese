"""A comment on a task's document that names its agent is answered in its
thread by the thread's own session, not by a turn of the task.

- The agent's answer is its reply in the thread, under the agent's name.
- One thread is one session: a later question in the thread goes to the same one.
- What the session changes in the document is recorded as done by the agent
  for the person who asked.
- Its tools act with the credential minted for the question: they read what the
  asker may read, and stop working once the answer is over.
- A session that fails still leaves the thread an answer from the agent.

The session host is faked at its boundary (``SessionHost``); the session's
tools reach the platform the way a real session's do, as the request each table
tool plans (`sandbox/cheese`), with the question's credential. The
collaboration service is tests/support/collab.py.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

import httpx
import pytest

from app.api.deps import (
    consumptions_for,
    get_chat_service,
    get_consumptions,
    get_session_host,
)
from app.domain.agent.document import question as doc_question
from app.domain.agent.harness.pi import catalog
from app.domain.agent.reads import Read
from app.domain.agent.service import AgentMessage, AgentResult
from app.domain.agent.session_host.contract import SessionRef, StartAbandoned
from app.domain.memory.files import MemoryFileScope
from app.domain.memory.files_store import MemoryFileStore
from app.main import app
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_doc_edits import ALICE_PARAGRAPH, _doc, _document
from tests.support.living_doc import document_of

#: The environment of the session whose prompt is being answered: what its
#: tools are planned with (``CHEESE_PROJECT``, ``CHEESE_DOCUMENT``…).
_SESSION_ENV: ContextVar[dict | None] = ContextVar("session_env", default=None)


def _chat():
    """The chat service the app is serving this test with."""
    return app.dependency_overrides.get(get_chat_service, get_chat_service)()


class FakeSessions:
    """The session host: every prompt sent to a session, answered by
    ``script`` with the credential the prompt's tools act with."""

    def __init__(self) -> None:
        #: (the session, what it was told), in order.
        self.asked: list[tuple[SessionRef, str]] = []
        self.script: Callable[..., Awaitable[tuple[str, str | None]]] | None = None
        self._answers: dict[SessionRef, tuple[str, str, str | None]] = {}
        #: The host has no memory for a session until this is set.
        self.room = threading.Event()
        self.room.set()
        #: Set while a start waits for the host, and while an answer is held.
        self.waiting = threading.Event()
        #: An answer that writes this much and then goes on until stopped.
        self.held: str | None = None
        self.stopped = threading.Event()
        #: Each session's environment, as it was started.
        self.envs: dict[SessionRef, dict] = {}

    def available(self) -> bool:
        return True

    async def start(self, ref, spec, access, *, on_wait=None, give_up=None):
        self.envs[ref] = dict(spec.env)
        if self.room.is_set():
            return
        if on_wait is not None:
            await on_wait()
        self.waiting.set()
        while not self.room.is_set():
            if give_up is not None and await give_up():
                raise StartAbandoned("stopped waiting")
            await asyncio.sleep(0.05)

    async def send(self, ref, prompt, *, work_id):
        self.asked.append((ref, prompt.text))
        answer, error = "好的。", None
        if self.script is not None:
            token = _SESSION_ENV.set(self.envs.get(ref, {}))
            try:
                answer, error = await self.script(prompt.acting, prompt.text)
            finally:
                _SESSION_ENV.reset(token)
        self._answers[ref] = (str(work_id), answer, error)

    async def read(self, ref, *, recovered=False):
        work, answer, error = self._answers.pop(ref)
        if self.held is not None:
            yield Read(work, AgentMessage(self.held))
            self.waiting.set()
            while not self.stopped.is_set():
                await asyncio.sleep(0.05)
            yield Read(work, AgentResult("aborted", None, is_error=True))
            return
        if answer:
            yield Read(work, AgentMessage(answer))
        yield Read(work, AgentResult(error or "", None, is_error=bool(error)))

    async def status(self, ref):
        return None

    async def stop(self, ref) -> bool:
        self.stopped.set()
        return False


@pytest.fixture
def sessions():
    fake = FakeSessions()
    questions = consumptions_for(fake, _chat())  # type: ignore[arg-type]
    app.dependency_overrides[get_session_host] = lambda: fake
    app.dependency_overrides[get_consumptions] = lambda: questions
    yield fake
    app.dependency_overrides.pop(get_session_host, None)
    app.dependency_overrides.pop(get_consumptions, None)


async def _tool(
    credential: str, name: str, args: dict, env: dict | None = None
) -> httpx.Response:
    """A tool call as the session makes it: the request the table plans for it
    in the session's environment (the one answering now, unless ``env``), with
    the question's credential."""
    plan = catalog._module().request_plan(name, args, env or _SESSION_ENV.get() or {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://platform"
    ) as platform:
        return await platform.request(
            plan["method"],
            plan["path"],
            json=plan.get("body"),
            headers={"X-Cheese-Token": credential},
        )


def _place(client, task: str) -> tuple[str, str]:
    """The task's project and the room it is in."""
    data = client.get(
        f"/topics/{task}/task", headers=session_auth_headers("alice")
    ).json()["data"]
    return data["project_id"], data["room_id"]


def _comments(client, task: str) -> str:
    return f"/documents/{document_of(client, task)}/comments"


def _comment(client, task: str, content: str, *, by: str = "alice") -> str:
    response = client.post(
        _comments(client, task),
        json={"content": content, "quote": "范围"},
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _thread(client, task: str, root: str) -> dict:
    return client.get(
        f"{_comments(client, task)}/{root}/thread",
        headers=session_auth_headers("alice"),
    ).json()["data"]


def _reply(client, task: str, root: str, content: str, *, by: str = "alice"):
    response = client.post(
        f"{_comments(client, task)}/{root}/replies",
        json={
            "operation_id": str(uuid.uuid4()),
            "expected_revision": _thread(client, task, root)["revision"],
            "content": content,
        },
        headers=session_auth_headers(by),
    )
    assert response.status_code == 200, response.text


def _answers(client, task: str, root: str, seat: str, count: int = 1) -> list[str]:
    """The agent's replies in the thread, once there are ``count`` of them."""
    deadline = time.monotonic() + 20
    while True:
        replies = [
            r["comment"]["content"]
            for r in _thread(client, task, root)["replies"]
            if r["comment"]["author"] == seat
        ]
        if len(replies) >= count or time.monotonic() > deadline:
            return replies
        time.sleep(0.1)


def test_a_comment_naming_the_agent_is_answered_in_its_thread(client, sessions):
    task, seat, _ = _document(client)

    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, task, root, seat) == ["好的。"]
    [(session, question)] = sessions.asked
    assert root in session.home
    assert "这里的范围指什么？" in question
    assert ALICE_PARAGRAPH in question


def test_the_thread_list_says_while_the_agent_is_answering(client, sessions):
    task, seat, _ = _document(client)
    seen: list = []
    threads = f"{_comments(client, task)}/threads"

    async def look(credential, question):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://platform"
        ) as page:
            listed = await page.get(threads, headers=session_auth_headers("alice"))
        seen.extend(t["answering"] for t in listed.json()["data"]["data"])
        return "好的。", None

    sessions.script = look
    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")

    assert _answers(client, task, root, seat) == ["好的。"]
    assert seen == ["working"]
    listed = client.get(threads, headers=session_auth_headers("alice")).json()["data"][
        "data"
    ]
    assert [t["answering"] for t in listed] == [None]


def test_a_comment_naming_nobody_or_a_person_asks_nothing(client, sessions):
    task, seat, _ = _document(client)

    root = _comment(client, task, "<@bob> 回头自己再看")
    _comment(client, task, "范围要再定一下")
    time.sleep(1)

    assert sessions.asked == []
    assert _thread(client, task, root)["replies"] == []


def test_a_later_question_in_the_thread_goes_to_the_same_session(client, sessions):
    task, seat, _ = _document(client)
    root = _comment(client, task, "这里要不要展开")

    _reply(client, task, root, f"<@{seat}> 你来看看", by="bob")

    assert _answers(client, task, root, seat) == ["好的。"]
    _reply(client, task, root, f"<@{seat}> 再短一点")
    assert len(_answers(client, task, root, seat, count=2)) == 2
    first, second = sessions.asked
    assert first[0] == second[0] and root in first[0].home
    assert "这里要不要展开" in first[1] and "你来看看" in first[1]


def test_what_the_session_changes_is_recorded_as_asked_by_the_commenter(
    client, sessions
):
    task, seat, _ = _document(client)

    async def edit(credential, question):
        response = await _tool(
            credential,
            "cheese_doc_edit",
            {"edits": [{"old": "讲范围", "new": "讲边界"}]},
        )
        assert response.status_code == 200, response.text
        return "改好了。", None

    sessions.script = edit
    root = _comment(client, task, f"<@{seat}> 把范围改成边界", by="bob")

    assert _answers(client, task, root, seat) == ["改好了。"]
    assert "李老师写的第二段，讲边界。" in _doc(client, task)["content"]
    latest = client.get(
        f"/documents/{document_of(client, task)}/history",
        headers=session_auth_headers("alice"),
    ).json()["data"]["versions"][-1]
    assert latest["actor"] == seat and latest["requested_by"] == "bob"


def test_the_tools_stop_working_once_the_answer_is_over(client, sessions, monkeypatch):
    task, seat, _ = _document(client)
    # An answer may take a second, and its credential lasts no longer.
    monkeypatch.setattr(doc_question, "ANSWER_S", 1.0)
    monkeypatch.setattr(doc_question, "CREDENTIAL_MARGIN_S", 0)
    held: dict[str, str] = {}

    held_env: dict = {}

    async def keep(credential, question):
        held["credential"] = credential
        held_env.update(_SESSION_ENV.get() or {})
        return "看过了。", None

    sessions.script = keep
    root = _comment(client, task, f"<@{seat}> 看一下")
    assert _answers(client, task, root, seat) == ["看过了。"]
    time.sleep(1.5)

    edit = client.portal.call(
        _tool,
        held["credential"],
        "cheese_doc_edit",
        {"edits": [{"old": "范围", "new": "x"}]},
        held_env,
    )
    assert edit.status_code == 401
    assert ALICE_PARAGRAPH in _doc(client, task)["content"]


def test_a_session_that_fails_still_answers_the_thread(client, sessions):
    task, seat, _ = _document(client)

    async def fail(credential, question):
        return "", "the model call failed"

    sessions.script = fail
    root = _comment(client, task, f"<@{seat}> 这段对吗")

    [answer] = _answers(client, task, root, seat)
    assert answer


def test_a_search_reaches_only_the_rooms_the_asker_may_read(client, sessions):
    task, seat, _ = _document(client)
    project, room = _place(client, task)
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
    root = _comment(client, task, f"<@{seat}> 里程碑定了几号？", by="bob")

    assert _answers(client, task, root, seat) == ["找到了。"]
    assert "三号" in found["text"]
    assert "七号" not in found["text"]


def test_the_team_memory_is_read_in_full(client, sessions):
    task, seat, _ = _document(client)
    project = uuid.UUID(_place(client, task)[0])

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
    root = _comment(client, task, f"<@{seat}> 什么时候能发版？", by="bob")

    assert _answers(client, task, root, seat) == ["记得。"]
    assert read["text"] == "部署走 CI，周五不发版。"


def _stop(client, task: str, root: str, *, by: str = "alice"):
    return client.post(
        f"{_comments(client, task)}/{root}/agent/stop",
        headers=session_auth_headers(by),
    )


def test_a_stopped_answer_keeps_what_was_written_and_says_it_stopped(client, sessions):
    task, seat, _ = _document(client)
    sessions.held = "范围指第二节列出的三个模块。"
    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")
    assert sessions.waiting.wait(20)

    stopped = _stop(client, task, root)

    assert stopped.status_code == 200, stopped.text
    assert _answers(client, task, root, seat) == [
        "范围指第二节列出的三个模块。\n\n已停止"
    ]


def test_a_question_waiting_for_a_full_host_can_be_stopped(client, sessions):
    task, seat, _ = _document(client)
    sessions.room.clear()
    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")
    assert sessions.waiting.wait(20)

    assert _stop(client, task, root).status_code == 200

    assert _answers(client, task, root, seat) == ["已停止"]
    assert sessions.asked == []


def test_a_question_waits_for_a_full_host_and_is_then_answered(client, sessions):
    task, seat, _ = _document(client)
    sessions.room.clear()
    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")
    assert sessions.waiting.wait(20)
    assert sessions.asked == []

    sessions.room.set()

    assert _answers(client, task, root, seat) == ["好的。"]


def test_only_someone_in_the_room_can_stop_its_answer(client, sessions):
    task, seat, _ = _document(client)
    sessions.held = "范围指"
    root = _comment(client, task, f"<@{seat}> 这里的范围指什么？")
    assert sessions.waiting.wait(20)

    refused = _stop(client, task, root, by="mallory")

    assert refused.status_code in (403, 404)
    sessions.stopped.set()


def test_a_document_in_no_room_is_answered_by_the_projects_own_agent(client, sessions):
    from app.domain.living_doc.models import Document

    task, seat, _ = _document(client)
    project = uuid.UUID(_place(client, task)[0])

    async def a_project_document() -> str:
        async with client.test_factory() as db:
            doc = Document(project_id=project, content="项目自己的文档：讲范围。")
            db.add(doc)
            await db.commit()
            return str(doc.id)

    doc = client.portal.call(a_project_document)
    threads = f"/documents/{doc}/comments"
    started = client.post(
        threads,
        json={"content": f"<@{seat}> 范围指什么？", "quote": "范围"},
        headers=session_auth_headers("alice"),
    )
    assert started.status_code == 200, started.text
    root = started.json()["data"]["id"]

    deadline = time.monotonic() + 20
    while True:
        replies = client.get(
            f"{threads}/{root}/thread", headers=session_auth_headers("alice")
        ).json()["data"]["replies"]
        if replies or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert [(r["comment"]["author"], r["comment"]["content"]) for r in replies] == [
        (seat, "好的。")
    ]
    [(session, question)] = sessions.asked
    assert "项目自己的文档：讲范围。" in question
    # It answers on the document, not in some task.
    assert sessions.envs[session]["CHEESE_DOCUMENT"] == doc
    assert "CHEESE_TOPIC" not in sessions.envs[session]
