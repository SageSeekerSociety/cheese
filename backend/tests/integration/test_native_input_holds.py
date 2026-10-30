"""Durable batch holds do not depend on process-local registration caches.

A child Python process queries PostgreSQL; it is not a restarted native executor.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent.turn_inputs import open_turns
from app.domain.block.models import Block, BlockKind
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import record_receipt, register_input
from app.domain.event_author import AuthorType
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_a_notice_reaches_the_working_session import (
    _heard,
    _KeepsWorking,
    _put_down,
    _service,
    _start_work,
)
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


async def _blocks(factory, project_id, topic_id, count=2):
    """Rows in the addressed room: a hold owns blocks, and registration proves
    they belong to that room before it will take them."""
    ids = tuple(uuid.uuid4() for _ in range(count))
    async with factory() as session:
        for block_id in ids:
            session.add(
                Block(
                    id=block_id,
                    project_id=project_id,
                    conversation_id=topic_id,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author="user-1",
                    content="answer",
                    meta={"consumed_turn": None},
                )
            )
        await session.commit()
    return ids


_QUERY = """
import asyncio, json, sys, uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.domain.delivery.receipts import held_blocks
async def run():
    engine = create_async_engine(sys.argv[1])
    try:
        async with async_sessionmaker(engine)() as session:
            ids = await held_blocks(session, project_id=uuid.UUID(sys.argv[2]),
                topic_id=uuid.UUID(sys.argv[3]), recipient_handle=sys.argv[4])
            print(json.dumps(sorted(str(block) for block in ids)))
    finally:
        await engine.dispose()
asyncio.run(run())
"""


def test_new_process_finds_only_the_addressed_batch_and_the_echo_releases_it(
    client,
):
    project = uuid.UUID(_project(client, "durable holds"))
    other_project = uuid.UUID(_project(client, "another project"))
    topic = uuid.UUID(_room(client, str(project), "holds"))
    other_topic = uuid.UUID(_room(client, str(project), "another room"))

    async def run():
        factory = client.test_request_factory
        identity = InputIdentity(
            project,
            topic,
            "cheese-test",
            "claude_code",
            str(uuid.uuid4()),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        blocks = await _blocks(factory, identity.project_id, identity.conversation_id)
        effects = InputEffects(held_block_ids=blocks)
        async with factory() as session:
            await register_input(session, identity, effects)
            await session.commit()
        for field, value in [
            ("project_id", other_project),
            ("conversation_id", other_topic),
            ("recipient_handle", "another-seat"),
        ]:
            other = replace(identity, **{field: value, "input_id": uuid.uuid4()})
            foreign = await _blocks(
                factory, other.project_id, other.conversation_id, count=1
            )
            async with factory() as session:
                await register_input(
                    session, other, InputEffects(held_block_ids=foreign)
                )
                await session.commit()
        async with factory() as session:
            url = session.bind.url.render_as_string(hide_password=False)

        async def read_from_new_process():
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _QUERY,
                url,
                str(identity.project_id),
                str(identity.conversation_id),
                identity.recipient_handle,
                env={**os.environ, "PYTHONPATH": "."},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            return set(json.loads(stdout))

        assert await read_from_new_process() == {str(block) for block in blocks}
        async with factory() as session:
            await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.commit()
        # Read is delivered: the batch is no longer held, wherever it is asked.
        assert await read_from_new_process() == set()
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert row.settled_at is not None
            assert set(row.released_block_ids) == {str(block) for block in blocks}

    client.portal.call(run)


def _close(client, task_id: uuid.UUID) -> None:
    r = client.post(
        f"/topics/{task_id}/close", json={}, headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text


def test_closing_a_task_again_gives_up_on_an_input_said_into_its_earlier_work(
    client,
):
    """Closing a task gives up on every input still unread in it, whatever work
    it was filed under. A message said, after a reopen, into the work an
    earlier close had already stopped belongs to no turn the second close ends;
    left unread, it held its seat against every later message for good."""
    project = post_project(client, {"name": "Closing twice"}, owner="alice")
    data = project.json()["data"]
    task_id = uuid.UUID(open_task(client, data["root_topic_id"])["id"])
    seat = "cheese-seat"
    earlier = uuid.uuid4()
    factory = client.test_request_factory

    async def work_on_it():
        now = datetime.now(UTC)
        async with factory() as session:
            await AgentTurnRepository(session).open(
                turn_id=earlier,
                conversation_id=task_id,
                continuation_id=earlier,
                author="alice",
                content="work",
                is_resume=False,
                resendable=True,
                started_at=now,
                delivered_at=now,
                agent_handle=seat,
            )
            await session.commit()

    client.portal.call(work_on_it)
    _close(client, task_id)
    client.portal.call(open_turns, factory)
    r = client.post(f"/topics/{task_id}/reopen", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text

    message = InputIdentity(
        uuid.UUID(data["id"]),
        task_id,
        seat,
        "claude_code",
        str(uuid.uuid4()),
        uuid.uuid4(),
        earlier,
    )

    async def say_into_the_earlier_work():
        async with factory() as session:
            await register_input(session, message, InputEffects())
            await session.commit()

    client.portal.call(say_into_the_earlier_work)
    _close(client, task_id)
    client.portal.call(open_turns, factory)

    async def outcome():
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == message.input_id)
            )
            return row.terminated_at, await seat_has_unfinished_input(
                session, task_id, seat
            )

    given_up_at, holds = client.portal.call(outcome)
    assert given_up_at is not None
    assert holds is False


def test_a_message_in_a_closed_task_is_not_handed_to_its_working_session(client):
    """A closed task's work is over and its session is being stopped: a message
    said there is not handed into that session as more of the work, and no
    input is registered for it."""
    project = post_project(client, {"name": "Closed task"}, owner="alice")
    data = project.json()["data"]
    room = data["root_topic_id"]
    seat = room_agent_seat(client, room)
    task_id = uuid.UUID(open_task(client, room)["id"])
    channel = _KeepsWorking()
    service = _service(client, channel)
    session = SessionRef(uuid.UUID(data["id"]), task_id, seat, harness=CLAUDE_CODE)

    client.portal.call(_start_work, channel, session)
    try:
        _close(client, task_id)

        async def say_it():
            delivered = await service.merge_into_running_session(
                task_id, [], "顺便把首页也改了", "alice", None, seat
            )
            async with client.test_request_factory() as db:
                rows = await db.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == task_id)
                )
                return delivered, list(rows)

        delivered, registered = client.portal.call(say_it)
        assert delivered is None
        assert registered == []
        assert not any("顺便把首页也改了" in s for s in _heard(channel, task_id, seat))
    finally:
        client.portal.call(_put_down, channel, session)
