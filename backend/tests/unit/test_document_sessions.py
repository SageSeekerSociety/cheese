"""A document thread's 芝士 on the session host: the pinned pi, started by the
real launch, with the platform faked as in test_personal_sessions.

Rules held here:

* the session has the document's tools and nothing of pi's own, and no machine
  is touched;
* the model is reached with the room's credential for the thread, so what it
  spends is the project's; the tools act with the credential minted for the
  question, at the platform's own routes for the document;
* a thread's next question finds the session's conversation, and two threads
  are two sessions.
"""

import dataclasses
import json
import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.agent.document import session as doc_session
from app.domain.agent.document.question import Asked, Bound, Surroundings
from app.domain.agent.session_host.answer import Answer, Tool, ask
from app.domain.agent.session_host.contract import Prompt
from app.domain.agent.session_host.host import SessionHost
from tests.support.session_host import DEVICE, Host, install_pi, stop_all
from tests.unit.test_personal_sessions import Platform

PROJECT = uuid.uuid4()
ROOM = uuid.uuid4()
DOCUMENT = uuid.uuid4()
AGENT = Bound(
    agent_handle="agent-seat",
    agent_name="芝士",
    model="fixture-model",
    wire_model="fixture-model",
    supply="gateway",
)
AROUND = Surroundings(
    content="", messages="", charter="本项目做存储选型。", memory=None
)


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "host"
    install_pi(home)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    hub = Host(home)
    yield hub, SessionHost(hub)
    stop_all(home)


@pytest.fixture
def platform(monkeypatch):
    made: list[Platform] = []

    def make(steps) -> Platform:
        fake = Platform(steps)
        monkeypatch.setattr(settings, "agent_session_api_base", fake.url)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


def _session(thread: uuid.UUID | None = None, *, machine: dict | None = None):
    """A thread's session as the document's 芝士 starts it, with the room's
    machine lent to it when there is one."""
    return doc_session.session_for(
        asked=Asked(project_id=PROJECT, document_id=DOCUMENT, room_id=ROOM),
        key=thread or uuid.uuid4(),
        bound=AGENT,
        around=dataclasses.replace(AROUND, machine=machine),
        where="thread",
    )


def _credential() -> str:
    return mint_delegated_credential(
        user_id=1,
        handle="alice",
        agent="agent-seat",
        project_id=str(PROJECT),
        topic_id=str(ROOM),
        work=str(uuid.uuid4()),
        read_only=False,
        ttl_s=300,
    )


async def _ask(sessions, started, text, credential: str = "") -> list:
    work = uuid.uuid4()
    return [
        event
        async for event in ask(
            sessions,
            *started,
            Prompt(work, text, acting=credential or _credential()),
            work_id=work,
            ceiling_s=120,
        )
    ]


@pytest.mark.anyio
async def test_a_thread_has_the_documents_tools_and_acts_for_the_asker(host, platform):
    hub, sessions = host
    fake = platform([{"tool": "cheese_doc_get"}, {"text": "读过了。"}])
    started = _session()
    acting = _credential()

    events = await _ask(sessions, started, "这里的范围指什么？", acting)

    assert events[-1] == Answer("读过了。")
    assert Tool("cheese_doc_get") in events
    first = fake.requests[0]
    # The document's own tools and the project lookups; no machine is lent
    # here, so none of pi's own.
    assert sorted(t["function"]["name"] for t in first["tools"]) == [
        "cheese_attachment_read",
        "cheese_doc_edit",
        "cheese_doc_get",
        "cheese_memory_read",
        "cheese_project_search",
    ]
    system = first["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert "本项目做存储选型。" in system
    _, _, access = started
    assert set(fake.model_auth) == {f"Bearer {access.credential}"}
    assert fake.tool_calls == [(f"GET /documents/{DOCUMENT}", acting)]
    ran = [argv for argv in hub.execs if argv != ["cat", "/proc/meminfo"]]
    assert ran and all(argv == ["python3", "-"] for argv in ran)


@pytest.mark.anyio
async def test_a_thread_keeps_its_conversation_and_threads_are_apart(host, platform):
    hub, sessions = host
    fake = platform(
        [{"text": "记住了。"}, {"text": "另一串。"}, {"text": "PINEAPPLE。"}]
    )
    thread = uuid.uuid4()

    await _ask(sessions, _session(thread), "The password is PINEAPPLE.")
    await _ask(sessions, _session(), "别的评论串")
    events = await _ask(sessions, _session(thread), "What was the password?")

    assert events[-1] == Answer("PINEAPPLE。")
    other = json.dumps(fake.requests[1]["messages"], ensure_ascii=False)
    assert "PINEAPPLE" not in other
    asked = json.dumps(fake.requests[2]["messages"], ensure_ascii=False)
    assert "The password is PINEAPPLE." in asked
    ref, _, _ = _session(thread)
    assert hub.alive(ref.state)
