"""The digest runner loads each receiver's preferences with one batch query."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.domain.notification import digest
from app.domain.notification.models import ChannelDelivery
from app.domain.notification.preferences import DigestCadence, default_preferences
from app.domain.notification.preferences_models import PreferencesRepository
from app.domain.user.repositories import UserRepository


async def test_digest_reads_preferences_once_for_all_receivers(db_factory, monkeypatch):
    receiver_items = {}
    async with db_factory() as session:
        users = [
            await UserRepository(session).create_user(
                username=f"digest-batch-{index}",
                email=f"digest-batch-{index}@example.com",
            )
            for index in range(3)
        ]
        await PreferencesRepository(session).save(
            users[0].id,
            replace(default_preferences(), digest_cadence=DigestCadence.daily),
        )
        await PreferencesRepository(session).save(
            users[1].id,
            replace(default_preferences(), digest_cadence=DigestCadence.weekly),
        )
        # The third user's missing row exercises the default preferences.
        for index, user in enumerate(users):
            content = f"digest item for receiver {index}"
            receiver_items[user.id] = content
            session.add(
                ChannelDelivery(
                    id=uuid.uuid4(),
                    delivery_key=f"digest-batch-{index}",
                    channel="digest",
                    receiver_id=user.id,
                    payload={"content": content},
                    recorded_at=datetime.now(UTC) - timedelta(days=8),
                    state="pending",
                )
            )
        await session.commit()

    sent = []

    async def record_digest(_sessions, receiver_id, items):
        sent.append((receiver_id, items))
        return True

    monkeypatch.setattr(digest, "send_digest_email", record_digest)

    preference_queries = []
    engine = db_factory.kw["bind"].sync_engine

    def count_preference_query(_conn, _cursor, statement, _parameters, _context, _many):
        if "notification_preference" in statement.lower():
            preference_queries.append(statement)

    event.listen(engine, "before_cursor_execute", count_preference_query)
    try:
        counts = await digest.run_notification_digests(
            db_factory, now=datetime.now(UTC)
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_preference_query)

    assert counts == {"digests": 3, "items": 3}
    assert len(preference_queries) == 1
    assert {
        receiver_id: items[0]["content"] for receiver_id, items in sent
    } == receiver_items
