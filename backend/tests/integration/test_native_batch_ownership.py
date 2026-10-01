"""PostgreSQL batch contention, not process-local held-input caches.

External admission is counted only after the real registration commits. The
channel is a counter, not a native executor. A third connection verifies the
shared-block wait before allowing the first transaction to commit.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.block.models import AuthorType, Block, BlockKind, BlockReaction
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import (
    complete_work_inputs,
    held_blocks,
    record_receipt,
    register_input,
)
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


async def _blocks(factory, project, topic):
    ids = (uuid.uuid4(), uuid.uuid4())
    async with factory() as session:
        for block_id in ids:
            session.add(
                Block(
                    id=block_id,
                    project_id=project,
                    topic_id=topic,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author="user-1",
                    content="answer",
                    meta={"consumed_turn": None},
                )
            )
        await session.commit()
    return ids


def _identity(project, topic, receiver="cheese-test"):
    return InputIdentity(
        project,
        topic,
        receiver,
        "claude_code",
        str(uuid.uuid4()),
        uuid.uuid4(),
        uuid.uuid4(),
    )


async def _delivery(factory, identity):
    delivery_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        session.add(
            Delivery(
                id=delivery_id,
                event_id=uuid.uuid4(),
                topic_id=identity.topic_id,
                recipient_handle=identity.recipient_handle,
                dedup_key=str(uuid.uuid4()),
                type="mention",
                payload={},
                event_at=datetime.now(UTC),
                recorded_at=datetime.now(UTC),
                state="sending",
                attempt_id=attempt_id,
            )
        )
        await session.commit()
    return delivery_id, attempt_id


@pytest.mark.parametrize("same_input", [False, True])
def test_two_deliveries_compete_for_one_batch_without_reverse_lock_edge(
    client,
    monkeypatch,
    same_input,
):
    project = uuid.UUID(_project(client, "batch contention"))
    topic = uuid.UUID(_room(client, str(project), "shared batch"))

    async def run():
        factory = client.test_request_factory
        ids = await _blocks(factory, project, topic)
        identities = [_identity(project, topic), _identity(project, topic)]
        links = [await _delivery(factory, identity) for identity in identities]
        if same_input:
            identities[1], links[1] = identities[0], links[0]
        effects = [
            InputEffects(held_block_ids=ids, delivery_id=link[0], attempt_id=link[1])
            for link in links
        ]
        locked, release, second_entered = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        pids, decisions, admissions = {}, [], []
        original = AsyncSession.scalars
        holder = None

        async def pause_block_lock(session, statement, *args, **kwargs):
            result = await original(session, statement, *args, **kwargs)
            if (
                session is holder
                and getattr(statement, "_for_update_arg", None) is not None
            ):
                locked.set()
                await release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "scalars", pause_block_lock)

        async def transact(index):
            nonlocal holder
            async with factory() as session:
                pids[index] = await session.scalar(text("SELECT pg_backend_pid()"))
                if index == 0:
                    holder = session
                else:
                    await locked.wait()
                    second_entered.set()
                try:
                    await register_input(session, identities[index], effects[index])
                    await session.commit()
                except ValidationError:
                    await session.rollback()
                    decisions.append((index, "held"))
                else:
                    decisions.append((index, "registered"))
                    admissions.append(identities[index].input_id)

        async def observer():
            await second_entered.wait()
            async with factory() as session:
                while True:
                    blockers = await session.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pids[1]}
                    )
                    if pids[0] in blockers:
                        assert pids[0] != pids[1]
                        release.set()
                        return
                    await asyncio.sleep(0.01)

        async with asyncio.timeout(15):
            async with asyncio.TaskGroup() as group:
                group.create_task(transact(0))
                group.create_task(transact(1))
                group.create_task(observer())
        if same_input:
            assert sorted(decisions) == [(0, "registered"), (1, "registered")]
            # Same-ID registration retry verifies one persisted identity; runtime
            # runner dedup owns transport retries, not this registration function.
            assert set(admissions) == {identities[0].input_id}
        else:
            assert sorted(decisions) == [(0, "registered"), (1, "held")]
            assert admissions == [identities[0].input_id]
        async with factory() as session:
            rows = list(await session.scalars(select(NativeInput)))
            assert len(rows) == 1
            assert set(rows[0].held_block_ids) == {str(block) for block in ids}
            assert await held_blocks(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=identities[0].recipient_handle,
            ) == set(ids)

    client.portal.call(run)


@pytest.mark.parametrize("other_scope", ["seat", "topic", "project"])
def test_independent_batches_register_while_first_batch_is_locked(
    client,
    monkeypatch,
    other_scope,
):
    project = uuid.UUID(_project(client, "independent batches"))
    topic = uuid.UUID(_room(client, str(project), "first batch"))
    other_project = (
        uuid.UUID(_project(client, "other project"))
        if other_scope == "project"
        else project
    )
    other_topic = (
        uuid.UUID(_room(client, str(other_project), "other batch"))
        if other_scope != "seat"
        else topic
    )

    async def run():
        factory = client.test_request_factory
        first = _identity(project, topic)
        other = _identity(
            other_project,
            other_topic,
            "other-seat" if other_scope == "seat" else "cheese-test",
        )
        batches = [
            await _blocks(factory, project, topic),
            await _blocks(factory, other_project, other_topic),
        ]
        locked, release = asyncio.Event(), asyncio.Event()
        original = AsyncSession.scalars
        holder = None

        async def pause(session, statement, *args, **kwargs):
            rows = await original(session, statement, *args, **kwargs)
            if (
                session is holder
                and getattr(statement, "_for_update_arg", None) is not None
            ):
                locked.set()
                await release.wait()
            return rows

        monkeypatch.setattr(AsyncSession, "scalars", pause)

        async def hold():
            nonlocal holder
            async with factory() as session:
                holder = session
                await register_input(
                    session, first, InputEffects(held_block_ids=batches[0])
                )
                await session.commit()

        async def independent():
            await locked.wait()
            async with factory() as session:
                await register_input(
                    session, other, InputEffects(held_block_ids=batches[1])
                )
                await session.commit()
            assert not release.is_set()
            release.set()

        async with asyncio.timeout(15):
            async with asyncio.TaskGroup() as group:
                group.create_task(hold())
                group.create_task(independent())
        async with factory() as session:
            assert len(list(await session.scalars(select(NativeInput)))) == 2

    client.portal.call(run)


def test_only_consumption_by_registered_work_releases_an_initial_hold(client):
    project = uuid.UUID(_project(client, "hold completion"))
    topic = uuid.UUID(_room(client, str(project), "initial input"))

    async def run():
        factory = client.test_request_factory
        identity = _identity(project, topic)
        ids = await _blocks(factory, project, topic)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=ids))
            await session.commit()
        for evidence in ["accepted", "native_echo"]:
            async with factory() as session:
                await record_receipt(session, InputReceipt(identity, evidence))
                await session.commit()
            async with factory() as session:
                assert await held_blocks(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=identity.recipient_handle,
                ) == set(ids)
        async with factory() as session:
            await BlockRepository(session).mark_consumed(list(ids), uuid.uuid4())
            await session.commit()
        async with factory() as session:
            assert await held_blocks(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=identity.recipient_handle,
            ) == set(ids)
            await complete_work_inputs(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=identity.recipient_handle,
                harness=identity.harness,
                native_session_id=identity.native_session_id,
                work_id=identity.work_id,
            )
            await session.commit()
        async with factory() as session:
            assert (
                await held_blocks(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=identity.recipient_handle,
                )
                == set()
            )

    client.portal.call(run)


@pytest.mark.parametrize("invalid", ["missing", "topic", "project", "seen_by"])
def test_registration_cannot_own_foreign_blocks_or_mark_another_receiver(
    client, invalid
):
    project = uuid.UUID(_project(client, "block ownership"))
    topic = uuid.UUID(_room(client, str(project), "input"))
    other_project = (
        uuid.UUID(_project(client, "foreign project"))
        if invalid == "project"
        else project
    )
    other_topic = (
        uuid.UUID(_room(client, str(other_project), "foreign topic"))
        if invalid in ("topic", "project")
        else topic
    )

    async def run():
        factory = client.test_request_factory
        ids = await _blocks(factory, other_project, other_topic)
        if invalid == "missing":
            ids = (uuid.uuid4(),)
        identity = _identity(project, topic)
        async with factory() as session:
            with pytest.raises(ValidationError):
                await register_input(
                    session,
                    identity,
                    InputEffects(
                        held_block_ids=ids,
                        seen_block_ids=ids,
                        seen_by="someone-else"
                        if invalid == "seen_by"
                        else identity.recipient_handle,
                    ),
                )
            await session.rollback()
        async with factory() as session:
            assert list(await session.scalars(select(NativeInput))) == []
            assert list(await session.scalars(select(BlockReaction))) == []

    client.portal.call(run)
