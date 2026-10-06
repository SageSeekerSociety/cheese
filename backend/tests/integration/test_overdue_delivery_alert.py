"""A delivery to an agent that stays unsent is reported to a person.

Each refused attempt goes back to waiting and nothing logs it as an error, so a
delivery could loop for hours with nobody told (2026-10-05: 17 hours).
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.delivery.models import Delivery, TimedDelivery
from app.domain.delivery.overdue import report_overdue
from app.domain.delivery.timer import deliver_due
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


class _Alerts:
    def __init__(self) -> None:
        self.posted: list[str] = []

    async def post(self, text: str) -> None:
        self.posted.append(text)


@pytest.fixture
def alerts(monkeypatch) -> _Alerts:
    from app.core import alerting
    from app.core.config import settings

    box = _Alerts()
    monkeypatch.setattr(settings, "feishu_alert_webhook", "https://alerts.test/hook")
    monkeypatch.setattr(alerting, "_post", box.post)
    monkeypatch.setattr(alerting, "repeated", alerting._Repeats())
    monkeypatch.setattr(alerting, "budget", alerting._Budget())
    return box


class _Recorder:
    def submit(self, chat, topic_id, **kwargs):
        pass


def _owed_delivery(client, room: str, *, recorded_ago: timedelta, sent: bool):
    """A timed delivery to the room's agent, recorded ``recorded_ago``."""
    response = client.post(
        f"/topics/{room}/deliveries",
        json={
            "at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "content": "check the build again",
        },
    )
    assert response.status_code == 200, response.text
    timer_id = uuid.UUID(response.json()["data"]["id"])
    factory = client.test_request_factory

    async def age():
        await deliver_due(factory, chat=object(), runner=_Recorder())
        async with factory() as session:
            timer = await session.get(TimedDelivery, timer_id)
            row = await session.scalar(
                select(Delivery).where(Delivery.event_id == timer.event_id)
            )
            # Refused and waiting again, as an attempt that was not admitted
            # leaves it.
            row.state = "pending"
            row.lease_until = None
            row.recorded_at = datetime.now(UTC) - recorded_ago
            if sent:
                row.state = "received"
                row.sent_at = datetime.now(UTC)
            await session.commit()

    client.portal.call(age)


def _report(client, alerts: _Alerts) -> tuple[int, str]:
    """How many deliveries were reported overdue, and what the alerts said."""
    alerts.posted.clear()
    total = client.portal.call(report_overdue, client.test_request_factory)
    time.sleep(0.3)  # alerts go out on their own tasks
    return total, "\n".join(alerts.posted)


def test_a_delivery_owed_for_two_hours_is_reported(client, alerts):
    room = _room(client, _project(client, "overdue"), "stuck")
    before, _ = _report(client, alerts)
    _owed_delivery(client, room, recorded_ago=timedelta(hours=2), sent=False)

    total, posted = _report(client, alerts)

    assert total == before + 1
    assert room in posted


def test_a_recent_or_delivered_one_is_not(client, alerts):
    project = _project(client, "not overdue")
    recent = _room(client, project, "recent")
    delivered = _room(client, project, "delivered")
    before, _ = _report(client, alerts)
    _owed_delivery(client, recent, recorded_ago=timedelta(minutes=5), sent=False)
    _owed_delivery(client, delivered, recorded_ago=timedelta(hours=2), sent=True)

    total, posted = _report(client, alerts)

    assert total == before
    assert recent not in posted
    assert delivered not in posted
