"""Receipt settlement uses persisted identity, never a text or memory candidate.

These are real PostgreSQL transaction tests, not native-model consumption tests.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import record_receipt, register_input


async def _registered(factory):
    identity = InputIdentity(
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        recipient_handle="cheese-test",
        harness="claude_code",
        native_session_id=str(uuid.uuid4()),
        input_id=uuid.uuid4(),
        work_id=uuid.uuid4(),
    )
    delivery_id, attempt_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        session.add(
            Delivery(
                id=delivery_id,
                event_id=event_id,
                recipient_handle=identity.recipient_handle,
                topic_id=identity.topic_id,
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
    async with factory() as session:
        await register_input(
            session,
            identity,
            InputEffects(
                delivery_id=delivery_id,
                attempt_id=attempt_id,
            ),
        )
        await session.commit()
    return identity, delivery_id, attempt_id


@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "topic_id",
        "recipient_handle",
        "harness",
        "native_session_id",
        "input_id",
        "work_id",
    ],
)
def test_wrong_receiver_cannot_settle_another_delivery(client, field):
    async def run():
        factory = client.test_request_factory
        identity, delivery_id, _ = await _registered(factory)
        wrong = (
            uuid.uuid4() if isinstance(getattr(identity, field), uuid.UUID) else "other"
        )
        async with factory() as session:
            result = await record_receipt(
                session,
                InputReceipt(replace(identity, **{field: wrong}), "native_echo"),
            )
            assert result is None
            await session.commit()
        async with factory() as session:
            assert (await session.get(Delivery, delivery_id)).state == "sending"
            row = await session.scalar(select(NativeInput))
            assert row.echoed_at is None and row.settled_at is None

    client.portal.call(run)


def test_rpc_acceptance_does_not_settle_and_fresh_session_can_reconcile_uncertain(
    client,
):
    async def run():
        factory = client.test_request_factory
        identity, delivery_id, _ = await _registered(factory)
        async with factory() as session:
            await record_receipt(session, InputReceipt(identity, "accepted"))
            await session.commit()
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.accepted_at is not None
            assert row.echoed_at is None and row.settled_at is None
            delivery = await session.get(Delivery, delivery_id)
            assert delivery.state == "sending" and delivery.sent_at is None
            delivery.state = "uncertain"
            await session.commit()
        # No ChatService instance or in-memory registration survives this boundary.
        async with factory() as session:
            row = await record_receipt(session, InputReceipt(identity, "native_echo"))
            assert row is not None
            await session.commit()
            settled = row.settled_at
        async with factory() as session:
            row = await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.commit()
            assert row.settled_at == settled
            assert (await session.get(Delivery, delivery_id)).state == "received"

    client.portal.call(run)


def test_failed_receipt_transaction_can_be_retried_after_session_loss(client):
    async def run():
        factory = client.test_request_factory
        identity, delivery_id, _ = await _registered(factory)
        async with factory() as session:
            await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.flush()
            # Failure before commit rolls all effects back, including receipt facts.
            await session.rollback()
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.echoed_at is None and row.settled_at is None
            assert (await session.get(Delivery, delivery_id)).state == "sending"
        async with factory() as session:
            await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.commit()
        async with factory() as session:
            assert (await session.get(Delivery, delivery_id)).state == "received"

    client.portal.call(run)


def test_late_receipt_cannot_settle_a_replaced_attempt(client):
    async def run():
        factory = client.test_request_factory
        identity, delivery_id, _ = await _registered(factory)
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            delivery.attempt_id = uuid.uuid4()
            await session.commit()
        async with factory() as session:
            assert (
                await record_receipt(session, InputReceipt(identity, "native_echo"))
                is None
            )
            await session.commit()
        async with factory() as session:
            assert (await session.get(Delivery, delivery_id)).state == "sending"
            assert (await session.scalar(select(NativeInput))).settled_at is None

    client.portal.call(run)


@pytest.mark.parametrize("first", ["registration", "echo"])
def test_registration_retry_and_echo_can_overlap(client, monkeypatch, first):
    async def run():
        factory = client.test_request_factory
        identity, delivery_id, attempt_id = await _registered(factory)
        effects = InputEffects(delivery_id=delivery_id, attempt_id=attempt_id)
        locked, release = asyncio.Event(), asyncio.Event()
        pids = {}
        original = AsyncSession.scalar
        holder = None

        async def pause_first_lock(session, statement, *args, **kwargs):
            result = await original(session, statement, *args, **kwargs)
            if (
                session is holder
                and getattr(statement, "_for_update_arg", None) is not None
                and not locked.is_set()
            ):
                locked.set()
                await release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "scalar", pause_first_lock)

        async def transact(name):
            nonlocal holder
            async with factory() as session:
                pids[name] = await original(session, text("SELECT pg_backend_pid()"))
                if name == first:
                    holder = session
                else:
                    await locked.wait()
                if name == "registration":
                    await register_input(session, identity, effects)
                else:
                    assert (
                        await record_receipt(
                            session, InputReceipt(identity, "native_echo")
                        )
                        is not None
                    )
                await session.commit()

        async def observe_wait():
            await locked.wait()
            other = "echo" if first == "registration" else "registration"
            async with factory() as observer:
                while True:
                    if other in pids:
                        blockers = await original(
                            observer,
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": pids[other]},
                        )
                        if pids[first] in blockers:
                            assert pids[first] != pids[other]
                            release.set()
                            return
                    await asyncio.sleep(0.01)

        async with asyncio.timeout(15):
            async with asyncio.TaskGroup() as group:
                group.create_task(transact("registration"))
                group.create_task(transact("echo"))
                group.create_task(observe_wait())
        async with factory() as session:
            rows = (await session.scalars(select(NativeInput))).all()
            assert len(rows) == 1
            assert rows[0].input_id == identity.input_id
            assert rows[0].attempt_id == attempt_id
            assert rows[0].echoed_at is not None
            assert rows[0].settled_at is not None
            assert (await session.get(Delivery, delivery_id)).state == "received"

    client.portal.call(run)
