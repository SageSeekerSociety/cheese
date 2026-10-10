"""A delivery the room keeps refusing is retried less and less often.

Every retry is a whole turn: it takes the agent's seat and brings its session
up before the input is refused again. A fixed short wait turned a permanent
refusal into a loop that kept the seat busy, and the people in the room waited
behind it.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.delivery.agent import (
    RETRY_CAP_SECONDS,
    RETRY_SECONDS,
    dispatch_pending,
    run_attempt,
)
from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.delivery.timer import deliver_due
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


class _Recorder:
    def __init__(self):
        self.attempts = []

    def submit(self, chat, topic_id, **kwargs):
        self.attempts.append((kwargs["delivery_id"], kwargs["turn_id"]))


async def _refused():
    """An attempt whose input never reached the session."""


def test_each_refused_attempt_waits_longer_up_to_a_cap(client):
    project = _project(client, "refused delivery")
    room = _room(client, project, "refusing")
    response = client.post(
        f"/topics/{room}/deliveries",
        json={
            "at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "content": "an instruction the room refuses",
        },
    )
    assert response.status_code == 200
    timer_id = uuid.UUID(response.json()["data"]["id"])
    factory = client.test_request_factory

    async def run():
        recorder = _Recorder()
        await deliver_due(factory, chat=object(), runner=recorder)
        waits = []
        for _ in range(8):
            [(delivery_id, attempt_id)] = recorder.attempts
            recorder.attempts.clear()
            await run_attempt(
                factory, delivery_id, attempt_id, _refused(), chat=object()
            )
            async with factory() as session:
                row = await session.get(Delivery, delivery_id)
                assert row.state == "pending" and row.sent_at is None
                waits.append((row.retry_at - datetime.now(UTC)).total_seconds())
                # The wait is honoured: nothing is claimed before it runs out.
                assert (
                    await dispatch_pending(factory, chat=object(), runner=recorder) == 0
                )
                # Time passes.
                row.retry_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            assert await dispatch_pending(factory, chat=object(), runner=recorder) == 1
        return delivery_id, waits

    delivery_id, waits = client.portal.call(run)

    # Twice as long each time, then held at the cap.
    assert RETRY_SECONDS - 5 < waits[0] <= RETRY_SECONDS
    for earlier, later in zip(waits, waits[1:], strict=False):
        assert abs(later - min(earlier * 2, RETRY_CAP_SECONDS)) < 5
    assert RETRY_CAP_SECONDS - 5 < waits[-1] <= RETRY_CAP_SECONDS
    assert max(waits) <= RETRY_CAP_SECONDS

    async def instruction():
        async with factory() as session:
            timer = await session.get(TimedDelivery, timer_id)
            delivery = await session.scalar(
                select(Delivery).where(Delivery.event_id == timer.event_id)
            )
            return delivery.id, delivery.payload["content"]

    # Still the same delivery, still owed: backing off never drops it.
    assert client.portal.call(instruction) == (
        delivery_id,
        "an instruction the room refuses",
    )
