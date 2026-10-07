"""An instruction to an AI teammate that has not started half an hour after it
was given stops being retried, and the conversation it was for is told.

The rules, as stated before the code was written:

- whoever waits in that conversation sees one line saying the teammate could
  not start, with a retry;
- the line is said once, however many times the ledger is swept afterwards.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery
from app.domain.delivery.timer import give_up_stale
from tests.integration.conftest import in_thread, post_project, room_agent_seat


def _stale_delivery(client, project: str, conversation: str, seat: str) -> uuid.UUID:
    """A message for the teammate, given 31 minutes ago and tried once."""

    async def record() -> uuid.UUID:
        async with client.test_factory() as session:
            instance = await session.scalar(
                select(AgentInstance.id).where(
                    AgentInstance.project_id == uuid.UUID(project),
                    AgentInstance.is_active.is_(True),
                )
            )
            long_ago = datetime.now(UTC) - timedelta(minutes=31)
            delivery = Delivery(
                event_id=uuid.uuid4(),
                recipient_handle=seat,
                agent_instance_id=instance,
                conversation_id=uuid.UUID(conversation),
                dedup_key=str(uuid.uuid4()),
                type="mention",
                payload={"content": f"<@{seat}> 看一下"},
                event_at=long_ago,
                recorded_at=long_ago,
                attempts=1,
            )
            session.add(delivery)
            await session.commit()
            return delivery.id

    return asyncio.run(record())


def _failure_lines(client, conversation: str) -> list[Block]:
    async def read() -> list[Block]:
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Block).where(
                        Block.conversation_id == uuid.UUID(conversation),
                        Block.meta["event_type"].as_string() == "turn_failed",
                    )
                )
            )

    return asyncio.run(read())


def test_an_instruction_that_never_starts_is_told_once_where_people_wait(client):
    data = post_project(client, {"name": "Never started"}, owner="alice").json()["data"]
    thread = in_thread(client, data["root_topic_id"], "alice")
    seat = room_agent_seat(client, thread)
    chat = client.app.dependency_overrides[get_chat_service]()
    runner = SimpleNamespace(submit=Mock())
    delivery_id = _stale_delivery(client, data["id"], thread, seat)

    # Whoever dispatches next leaves it alone; the sweep gives it up, once.
    client.portal.call(
        lambda: dispatch_pending(
            chat.session_factory, chat=chat, runner=runner, delivery_ids=[delivery_id]
        )
    )
    for _ in range(2):
        client.portal.call(lambda: give_up_stale(chat.session_factory, chat=chat))

    runner.submit.assert_not_called()
    lines = _failure_lines(client, thread)
    assert len(lines) == 1, "没开始的指令说了不止一遍，或者没说"
    assert (lines[0].meta or {}).get("retryable") is True
