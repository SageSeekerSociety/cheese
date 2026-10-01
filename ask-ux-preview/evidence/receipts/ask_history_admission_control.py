"""Remove only the two new protections; leave fixtures and native I/O intact."""
from contextlib import asynccontextmanager

import pytest


@pytest.fixture(autouse=True)
def previous_admission_and_recovery(monkeypatch):
    from app.domain.agent import answer_delivery
    from app.domain.agent.harness.claude_code.subscription import Subscription
    from app.domain.delivery.models import Delivery

    current = answer_delivery.admitted_initial

    @asynccontextmanager
    async def ask_only(chat, topic_id, delivery_id, attempt_id, content, **kwargs):
        async with chat.session_factory() as session:
            delivery = await session.get(Delivery, delivery_id) if delivery_id else None
            is_answer = delivery is not None and "answer_to" in delivery.payload
        if is_answer:
            async with current(chat, topic_id, delivery_id, attempt_id, content, **kwargs) as offered:
                yield offered
        else:
            yield False

    async def no_historical_reconciliation(self):
        pass

    monkeypatch.setattr(answer_delivery, "admitted_initial", ask_only)
    monkeypatch.setattr(Subscription, "reconcile_history", no_historical_reconciliation)
