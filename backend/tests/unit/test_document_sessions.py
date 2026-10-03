"""A document thread's 芝士 on the session host: the pinned pi, started by the
real launch, with the platform faked as in test_personal_sessions.

Rules held here:

* the session has the document's tools and nothing of pi's own, and no machine
  is touched;
* the model and the tools are reached with the room's credential for the
  thread, so what it spends is the project's;
* a thread's next question finds the session's conversation, and two threads
  are two sessions.
"""

import json
import uuid

import pytest

from app.api import doc_agent
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.harness.pi import document
from app.domain.agent.harness.pi.handless import Answered, HandlessSessions, Looking
from tests.support.session_host import DEVICE, Host, install_pi, stop_all
from tests.unit.test_personal_sessions import Platform

PROJECT = uuid.uuid4()
ROOM = uuid.uuid4()


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "host"
    install_pi(home)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    hub = Host(home)
    yield hub, HandlessSessions(hub)
    stop_all(home)


@pytest.fixture
def platform(monkeypatch):
    made: list[Platform] = []

    def make(steps) -> Platform:
        fake = Platform(steps, tools_path=doc_agent.TOOLS_PATH)
        monkeypatch.setattr(settings, "agent_session_api_base", fake.url)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


def _launch(thread: uuid.UUID | None = None) -> document.Launch:
    thread = thread or uuid.uuid4()
    return document.Launch(
        project_id=PROJECT,
        thread_id=thread,
        system_prompt=doc_agent.system_prompt("芝士", "本项目做存储选型。", None),
        tools_path=doc_agent.TOOLS_PATH,
        tools=doc_agent.TOOLS,
        token=mint_scoped_token(
            project_id=str(PROJECT),
            topic_id=str(ROOM),
            agent_handle="agent-seat",
            resource_id=str(thread),
        ),
        model="fixture-model",
    )


async def _ask(sessions, launch, text) -> list:
    return [
        event async for event in sessions.ask(launch, uuid.uuid4(), text, ceiling_s=120)
    ]


@pytest.mark.anyio
async def test_a_thread_has_the_documents_tools_and_the_rooms_credential(
    host, platform
):
    hub, sessions = host
    fake = platform([{"tool": "read_document"}, {"text": "读过了。"}])
    launch = _launch()

    events = await _ask(sessions, launch, "这里的范围指什么？")

    assert events[-1] == Answered("读过了。")
    assert Looking("read_document") in events
    first = fake.requests[0]
    # The document's own tools and the project lookups; no machine is lent
    # here, so none of pi's own.
    assert sorted(t["function"]["name"] for t in first["tools"]) == [
        "edit_document",
        "read_attachment",
        "read_document",
        "read_memory",
        "search_project",
    ]
    system = first["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert "本项目做存储选型。" in system
    assert set(fake.model_auth) == {f"Bearer {launch.token}"}
    assert [(name, token) for name, token, _ in fake.tool_calls] == [
        ("read_document", launch.token)
    ]
    ran = [argv for argv in hub.execs if argv != ["cat", "/proc/meminfo"]]
    assert ran and all(argv == ["python3", "-"] for argv in ran)


@pytest.mark.anyio
async def test_a_thread_keeps_its_conversation_and_threads_are_apart(host, platform):
    hub, sessions = host
    fake = platform(
        [{"text": "记住了。"}, {"text": "另一串。"}, {"text": "PINEAPPLE。"}]
    )
    thread = uuid.uuid4()

    await _ask(sessions, _launch(thread), "The password is PINEAPPLE.")
    await _ask(sessions, _launch(), "别的评论串")
    events = await _ask(sessions, _launch(thread), "What was the password?")

    assert events[-1] == Answered("PINEAPPLE。")
    other = json.dumps(fake.requests[1]["messages"], ensure_ascii=False)
    assert "PINEAPPLE" not in other
    asked = json.dumps(fake.requests[2]["messages"], ensure_ascii=False)
    assert "The password is PINEAPPLE." in asked
    assert hub.alive(document.state_dir(PROJECT, thread))
