"""A turn that reached nobody leaves nothing behind that refuses the next one.

When the write carrying a turn's prompt fails with no word from the session,
the room keeps the turn open until the orphan sweep finds it, closes it and
sends the message again in a new turn. The closed turn used to stay on the
service's list of live work for that teammate, so the next turn was the
second live origin on one seat: every question it asked was refused with 403
「无法确认原生提问会话和执行区间」, and it kept being reminded that it had
published nothing, for a turn nobody was running.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
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


class FirstSendLost(StubChannel):
    """The first prompt's write fails without the session saying anything;
    every later prompt arrives, and the session keeps working on it."""

    def __init__(self):
        super().__init__()
        self.lost = False

    async def call(self, handle, method, params):
        if method == "send" and not self.lost:
            self.lost = True
            raise RuntimeError("executor response interrupted")
        return await super().call(handle, method, params)

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.uses(topic_id, "Bash", agent=agent, command="sleep 600")


def _turns(client, room: str) -> list[AgentTurn]:
    async def read() -> list[AgentTurn]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(AgentTurn)
                    .where(AgentTurn.conversation_id == uuid.UUID(room))
                    .order_by(AgentTurn.started_at)
                )
            )

    return asyncio.run(read())


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def test_the_turn_that_resends_an_unheard_message_can_ask(client):
    data = post_project(client, {"name": "Unheard"}, owner="alice").json()["data"]
    # 芝士 answers in a 支线 of the channel.
    room = in_thread(client, data["root_topic_id"], "alice")
    channel = FirstSendLost()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/ask-unheard-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 核一下预算"})
        while ws.receive_json()["type"] != "user_block":
            pass
    _until(lambda: channel.lost and _turns(client, room), "the first send never ran")
    (unheard,) = _turns(client, room)
    assert unheard.delivered_at is None

    # The sweep finds the turn that reached nobody and sends its message again.
    runner = get_work_runner()
    assert client.portal.call(lambda: runner.sweep_orphans(service, min_age_s=0.0))
    _until(
        lambda: any(
            t.id != unheard.id and t.delivered_at for t in _turns(client, room)
        ),
        "the message was never sent again",
    )

    headers = agent_credential(data["id"], room, room_agent_seat(client, room))
    response = client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers)
    assert response.status_code == 200, response.text
