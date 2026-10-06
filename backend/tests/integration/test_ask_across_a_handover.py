"""A question a teammate asks while the backend changes hands is still asked.

During a rollout the incoming backend answers requests before it has taken the
running work over (`app.core.ownership`). A teammate in the middle of its turn
that asks the room a question in that window used to be refused with 403
「无法确认原生提问会话和执行区间」, as if it had asked outside any turn: only the
outgoing backend knew the turn was running. On dev that window opens on every
merge to main, for half a minute or more.
"""

import asyncio
import threading
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block
from app.main import app
from tests.ask_fixtures import agent_credential
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
    room_agent_seat,
)

QUESTION = {
    "questions": [
        {
            "question": "预算按哪个口径统计",
            "options": [{"text": "按部门"}, {"text": "按项目"}],
        }
    ]
}


class StillWorking(StubChannel):
    """The session takes its prompt and starts a long command."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


def _service(client, channel: StubChannel) -> ChatService:
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/ask-handover-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return service


def _turns(client, room: str) -> list[AgentTurn]:
    async def read() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn).where(
                        AgentTurn.conversation_id == uuid.UUID(room)
                    )
                )
            )

    return asyncio.run(read())


def _questions(client, room: str) -> list[str]:
    async def read() -> list[str]:
        async with client.test_factory() as session:
            blocks = await session.scalars(
                select(Block).where(Block.conversation_id == uuid.UUID(room))
            )
            return [b.content for b in blocks if (b.meta or {}).get("ask_group")]

    return asyncio.run(read())


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_a_question_asked_before_the_takeover_is_asked_once_it_lands(client):
    data = post_project(client, {"name": "Handover ask"}, owner="alice").json()["data"]
    # 芝士 answers in a 支线 of the channel.
    room = in_thread(client, data["root_topic_id"], "alice")
    before = StillWorking()
    _service(client, before)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 核一下预算"})
        while ws.receive_json()["type"] != "user_block":
            pass
    _until(
        lambda: any(t.delivered_at for t in _turns(client, room)),
        "the turn never reached its session",
    )

    # The outgoing backend lets go of the work. The incoming one already
    # serves requests, and knows nothing yet of the turn still running.
    runner = get_work_runner()
    client.portal.call(runner.let_go)
    client.portal.call(before.runtime.stop_listening)
    runner.hold_turns()
    after = StubChannel()
    after.root = before.root
    after.sessions = dict(before.sessions)
    for session in after.sessions.values():
        session.channel = after
    incoming = _service(client, after)

    headers = agent_credential(data["id"], room, room_agent_seat(client, room))
    answered = {}
    asking = threading.Thread(
        target=lambda: answered.setdefault(
            "response",
            client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers),
        )
    )
    asking.start()
    asking.join(1.0)
    assert "response" not in answered, answered["response"].text

    # The lock reaches the incoming backend: it listens to the session again
    # and picks the running turn up where it stands.
    client.portal.call(incoming.recover_sessions)
    runner.start_turns()
    asking.join(15)

    response = answered["response"]
    assert response.status_code == 200, response.text
    assert _questions(client, room) == ["预算按哪个口径统计"]
    (turn,) = _turns(client, room)
    assert turn.stopped_at is None


def test_a_question_with_no_turn_is_still_refused_after_the_takeover(client):
    """Waiting for the takeover is not a way around the rule: a seat that has
    no running turn once the work is here is refused, as before."""
    data = post_project(client, {"name": "Handover idle"}, owner="alice").json()["data"]
    # 芝士 answers in a 支线 of the channel.
    room = in_thread(client, data["root_topic_id"], "alice")
    _service(client, StubChannel())
    runner = get_work_runner()
    runner.hold_turns()

    headers = agent_credential(data["id"], room, room_agent_seat(client, room))
    answered = {}
    asking = threading.Thread(
        target=lambda: answered.setdefault(
            "response",
            client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers),
        )
    )
    asking.start()
    asking.join(1.0)
    runner.start_turns()
    asking.join(15)

    response = answered["response"]
    assert response.status_code == 403, response.text
    assert "无法确认原生提问会话和执行区间" in response.text
    assert _questions(client, room) == []
