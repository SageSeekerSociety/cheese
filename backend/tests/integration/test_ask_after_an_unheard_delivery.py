"""A delivered message that reached nobody leaves no live turn behind either.

A message the delivery ledger hands to 芝士 (a mention it routes, a
platform instruction) runs as a turn of its own. When that turn's write fails with no
word from the session, the orphan sweep closes it, and the delivery keeps the
message: it is not sent again behind its back. The closed turn used to stay on
the service's list of live work for that teammate all the same, so the room
read as busy for a turn nobody runs (FB-72, after the sweep learned to let go
of the turns it re-sends itself). The next turn still runs, and asks.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.models import AgentInstance
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery
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


def _deliver(client, project: str, room: str, seat: str) -> uuid.UUID:
    """Put one message for the room's agent on the delivery ledger."""

    async def record() -> uuid.UUID:
        async with client.test_factory() as session:
            instance = await session.scalar(
                select(AgentInstance.id).where(
                    AgentInstance.project_id == uuid.UUID(project),
                    AgentInstance.is_active.is_(True),
                )
            )
            delivery = Delivery(
                event_id=uuid.uuid4(),
                recipient_handle=seat,
                agent_instance_id=instance,
                conversation_id=uuid.UUID(room),
                dedup_key=str(uuid.uuid4()),
                type="mention",
                payload={"content": f"<@{seat}> 按部门"},
                event_at=datetime.now(UTC),
                recorded_at=datetime.now(UTC),
            )
            session.add(delivery)
            await session.commit()
            return delivery.id

    return asyncio.run(record())


def test_a_question_after_an_unheard_delivery_is_not_refused(client):
    data = post_project(client, {"name": "Unheard delivery"}, owner="alice").json()[
        "data"
    ]
    room = in_thread(client, data["root_topic_id"], "alice")
    seat = room_agent_seat(client, room)
    channel = FirstSendLost()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/ask-unheard-delivery-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    runner = get_work_runner()

    delivery_id = _deliver(client, data["id"], room, seat)
    assert client.portal.call(
        lambda: dispatch_pending(
            service.session_factory,
            chat=service,
            runner=runner,
            delivery_ids=[delivery_id],
        )
    )
    _until(lambda: channel.lost and _turns(client, room), "the delivery never ran")
    (unheard,) = _turns(client, room)
    assert unheard.delivered_at is None

    # The sweep closes the turn; the ledger, not the sweep, owns the message.
    client.portal.call(lambda: runner.sweep_orphans(service, min_age_s=0.0))
    _until(
        lambda: _turns(client, room)[0].stopped_at is not None,
        "the sweep never closed the unheard turn",
    )
    assert not service.has_running_turn(uuid.UUID(room))

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        post_message(client, room, "alice", {"content": "@芝士 好了吗"})
        while ws.receive_json()["type"] != "user_block":
            pass
    _until(
        lambda: any(
            t.id != unheard.id and t.delivered_at for t in _turns(client, room)
        ),
        "the next message never started a turn",
    )

    headers = agent_credential(data["id"], room, seat)
    response = client.post(f"/topics/{room}/asks", json=QUESTION, headers=headers)
    assert response.status_code == 200, response.text
