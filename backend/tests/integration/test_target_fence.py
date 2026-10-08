"""A confirmed missing target ends only the attempt still owned by its caller.

Both production transaction owners use real PostgreSQL; no receiver is replaced.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domain.agent.input_registration import input_registrar
from app.domain.agent.turn.state.live import LiveWork
from app.domain.delivery import agent
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.models import Delivery, NativeInput


@pytest.mark.parametrize("caller", ["begin_send", "input_registrar"])
@pytest.mark.parametrize(
    "state, stale, expiry, expected",
    [
        ("claimed", False, "live", "failed"),
        ("claimed", True, "live", "claimed"),
        ("claimed", False, "expired", "claimed"),
        ("claimed", False, "during_fence", "claimed"),
        ("received", False, "live", "received"),
        ("uncertain", False, "live", "uncertain"),
    ],
    ids=[
        "missing-target",
        "stale-attempt",
        "expired-attempt",
        "expires-during-fence",
        "received-attempt",
        "uncertain-attempt",
    ],
)
def test_a_missing_target_ends_only_a_current_attempt(
    client, monkeypatch, caller, state, stale, expiry, expected
):
    async def run():
        factory = client.test_request_factory
        stamp = datetime.now(UTC)
        lease = stamp + timedelta(minutes=-1 if expiry == "expired" else 2)
        delivery_id, recorded_attempt = uuid.uuid4(), uuid.uuid4()
        requested_attempt = uuid.uuid4() if stale else recorded_attempt
        identity = InputIdentity(
            uuid.uuid4(),
            uuid.uuid4(),
            "cheese-target-fence",
            "claude_code",
            "native-parent",
            uuid.uuid4(),
            uuid.uuid4(),
        )
        async with factory() as session:
            session.add(
                Delivery(
                    id=delivery_id,
                    event_id=uuid.uuid4(),
                    recipient_handle=identity.recipient_handle,
                    conversation_id=uuid.uuid4(),  # The addressed task was deleted.
                    agent_instance_id=uuid.uuid4(),
                    state=state,
                    attempt_id=recorded_attempt,
                    lease_until=lease,
                    dedup_key=str(uuid.uuid4()),
                    type="task_instruction",
                    payload={},
                    event_at=stamp,
                    recorded_at=stamp,
                )
            )
            await session.commit()
        if expiry == "during_fence":
            moments = iter((stamp, stamp + timedelta(minutes=3)))
            monkeypatch.setattr(agent, "now", lambda: next(moments))
        live = LiveWork()
        with pytest.raises(ValidationError):
            if caller == "begin_send":
                await agent.begin_send(factory, delivery_id, requested_attempt)
            else:
                registrar = input_registrar(
                    factory,
                    InputEffects(delivery_id=delivery_id, attempt_id=requested_attempt),
                    live,
                    probe_unread=True,
                    fence_delivery=True,
                )
                await registrar(identity)
        assert live.unread_inputs == {}
        async with factory() as session:
            row = await session.get(Delivery, delivery_id)
            assert row.state == expected
            assert row.attempt_id == recorded_attempt
            assert row.sent_at is None
            if expected == "failed":
                assert row.last_error
                assert row.lease_until is None
            else:
                assert row.last_error is None
                assert row.lease_until == lease
            assert (
                await session.scalar(
                    select(NativeInput).where(NativeInput.input_id == identity.input_id)
                )
                is None
            )

    client.portal.call(run)
