"""A task made from a discussion starts with its AI teammate drafting the
document. While the document is empty, whoever opens the task is told where
that is, and when it failed they can have it tried again.

The rules, stated before the code:

- right after the task is made, it is being drafted;
- once the first instruction gave up, the task says it failed, and someone
  working the task can try again, which gives the same instruction again;
- only someone working the task tries again, and only after a failure;
- while the first turn runs, the task is being drafted, even before the
  session's receipt for the instruction is recorded; it failed only when that
  turn ended having said nothing;
- a task created empty has no such first turn;
- an instruction that has not started half an hour after it was given gives up
  instead of trying for ever.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _task_from_message(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    )
    channel = r.json()["data"]["id"]
    said = post_message(client, channel, "alice", {"content": "微信里打不开预览"})
    r = client.post(
        f"/blocks/{said['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return channel, r.json()["data"]["id"]


def _opening(client, task):
    r = client.get(f"/topics/{task}/task", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]["opening"]


def _openings(client, task):
    from app.domain.delivery.models import Delivery

    async def _rows():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(Delivery)
                    .where(
                        Delivery.conversation_id == uuid.UUID(task),
                        Delivery.payload["purpose"].astext == "opening",
                    )
                    .order_by(Delivery.recorded_at)
                )
            )

    return asyncio.run(_rows())


def _give_up(client, task):
    from app.domain.delivery.models import Delivery

    async def _fail():
        async with client.test_factory() as session:
            await session.execute(
                update(Delivery)
                .where(Delivery.conversation_id == uuid.UUID(task))
                .values(state="failed", last_error="gave up")
            )
            await session.commit()

    asyncio.run(_fail())


def test_a_new_task_is_being_drafted(client):
    _, task = _task_from_message(client)

    assert _opening(client, task) in ("drafting", "waiting")


def test_a_failed_opening_is_said_and_can_be_tried_again(client):
    _, task = _task_from_message(client)
    _give_up(client, task)
    assert _opening(client, task) == "failed"

    r = client.post(f"/topics/{task}/opening", headers=session_auth_headers("alice"))

    assert r.status_code == 200, r.text
    assert _opening(client, task) != "failed"
    first, again = _openings(client, task)
    assert again.payload["content"] == first.payload["content"]


def test_only_after_a_failure_and_only_by_someone_working_the_task(client):
    _, task = _task_from_message(client)
    early = client.post(
        f"/topics/{task}/opening", headers=session_auth_headers("alice")
    )
    assert early.status_code == 409
    _give_up(client, task)

    outsider = client.post(
        f"/topics/{task}/opening", headers=session_auth_headers("bob")
    )

    assert outsider.status_code == 403
    assert len(_openings(client, task)) == 1


def test_a_task_created_empty_has_no_first_turn(client):
    channel, _ = _task_from_message(client)
    r = client.post(
        f"/topics/{channel}/tasks",
        json={"title": "整理周报"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    assert _opening(client, r.json()["data"]["id"]) is None


def test_an_instruction_not_started_in_half_an_hour_gives_up(client):
    from app.api.deps import get_chat_service, get_work_runner
    from app.domain.delivery.models import Delivery
    from app.domain.delivery.timer import deliver_due

    _, task = _task_from_message(client)

    async def _age():
        async with client.test_factory() as session:
            await session.execute(
                update(Delivery)
                .where(Delivery.conversation_id == uuid.UUID(task))
                .values(
                    state="pending",
                    attempts=3,
                    lease_until=None,
                    retry_at=None,
                    recorded_at=datetime.now(UTC) - timedelta(minutes=31),
                )
            )
            await session.commit()

    asyncio.run(_age())
    # The ledger's sweep, which runs on a clock, in the app's own loop.
    chat = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(
        lambda: deliver_due(chat.session_factory, chat=chat, runner=get_work_runner())
    )

    assert _opening(client, task) == "failed"


def test_a_running_first_turn_is_drafting_until_it_ends_having_said_nothing(client):
    from app.domain.agent.models import AgentTurn
    from app.domain.delivery.models import Delivery

    _, task = _task_from_message(client)
    turn_id = uuid.uuid4()

    async def _sent_without_receipt():
        # The send returned and the receipt is not in yet: the ledger row reads
        # `uncertain` while the turn it started is running.
        async with client.test_factory() as session:
            session.add(
                AgentTurn(
                    id=turn_id,
                    continuation_id=turn_id,
                    conversation_id=uuid.UUID(task),
                    author="system",
                    started_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(Delivery)
                .where(Delivery.conversation_id == uuid.UUID(task))
                .values(state="uncertain", attempt_id=turn_id, attempts=1)
            )
            await session.commit()

    asyncio.run(_sent_without_receipt())
    assert _opening(client, task) == "drafting"

    async def _turn_ends():
        async with client.test_factory() as session:
            (await session.get(AgentTurn, turn_id)).stopped_at = datetime.now(UTC)
            await session.commit()

    asyncio.run(_turn_ends())
    assert _opening(client, task) == "failed"
