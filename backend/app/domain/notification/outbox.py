"""Transactional notification-channel intent and bounded external retries.

Email/push transports do not promise idempotent acceptance. A lost response is
retried at least once with a stable correlation key; duplicates remain possible.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.delivery.models import ChannelDelivery
from app.domain.notification.push import PUSHABLE, push_text

LEASE_SECONDS = 120


class ChannelIntentHandler:
    name = "external-channel-intent"

    def __init__(self, session, *, push_enabled):
        self.session = session
        self.push_enabled = push_enabled

    async def send_batch(self, deliveries):
        stamp = datetime.now(UTC)
        rows = []
        for delivery in deliveries:
            common = {
                "recipientId": delivery.recipient_id,
                "type": delivery.type.value,
                "payload": delivery.payload,
                "deliveryKey": delivery.delivery_key,
            }
            channels = [("email", common)]
            if self.push_enabled and delivery.type in PUSHABLE:
                title, body = push_text(delivery.type, delivery.payload)
                channels.append(
                    (
                        "push",
                        {
                            **common,
                            "title": title,
                            "body": body,
                            "projectId": delivery.payload.get("projectId"),
                            "topicId": delivery.payload.get("topicId"),
                        },
                    )
                )
            for channel, payload in channels:
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "delivery_key": delivery.delivery_key,
                        "channel": channel,
                        "receiver_id": delivery.recipient_id,
                        "payload": payload,
                        "recorded_at": stamp,
                    }
                )
        if rows:
            await self.session.execute(
                insert(ChannelDelivery)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_delivery_channel")
            )


async def drain_channel(sessions, *, channel, batch_size, max_attempts):
    if channel not in ("email", "push"):
        raise ValueError("Unknown notification channel")
    counts = {"processed": 0, "retried": 0, "dead_lettered": 0, "expired": 0}
    for _ in range(batch_size):
        stamp = datetime.now(UTC)
        async with sessions() as session:
            row = await session.scalar(
                select(ChannelDelivery)
                .where(
                    ChannelDelivery.channel == channel,
                    ChannelDelivery.state.in_(("pending", "sending")),
                    or_(
                        ChannelDelivery.lease_until.is_(None),
                        ChannelDelivery.lease_until <= stamp,
                    ),
                    or_(
                        ChannelDelivery.retry_at.is_(None),
                        ChannelDelivery.retry_at <= stamp,
                    ),
                )
                .order_by(ChannelDelivery.recorded_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                break
            if row.attempts >= max_attempts:
                row.state = "dead"
                row.last_error = (
                    row.last_error
                    or "Retry limit reached after an unacknowledged attempt"
                )
                await session.commit()
                counts["dead_lettered"] += 1
                continue
            row.state = "sending"
            row.attempts += 1
            row.claim_token = uuid.uuid4()
            row.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
            row_id, token, payload = row.id, row.claim_token, dict(row.payload)
            await session.commit()

        async def renew(row_id=row_id, token=token):
            while True:
                await asyncio.sleep(LEASE_SECONDS / 3)
                async with sessions() as session:
                    await session.execute(
                        update(ChannelDelivery)
                        .where(
                            ChannelDelivery.id == row_id,
                            ChannelDelivery.claim_token == token,
                            ChannelDelivery.state == "sending",
                        )
                        .values(
                            lease_until=datetime.now(UTC)
                            + timedelta(seconds=LEASE_SECONDS)
                        )
                    )
                    await session.commit()

        heartbeat = asyncio.create_task(renew())
        failure = None
        try:
            if channel == "email":
                from app.domain.notification.maintenance import send_email

                await send_email(sessions, payload)
            else:
                from app.domain.notification.push_delivery import send_push

                counts["expired"] += await send_push(sessions, payload)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"[:1000]
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        # No DB transaction is held while the provider is contacted. A stale
        # worker cannot acknowledge a claim already recovered by another worker.
        async with sessions() as session:
            row = await session.scalar(
                select(ChannelDelivery)
                .where(
                    ChannelDelivery.id == row_id,
                    ChannelDelivery.claim_token == token,
                    ChannelDelivery.state == "sending",
                )
                .with_for_update()
            )
            if row is None:
                continue
            row.lease_until = None
            row.last_error = failure
            if failure is None:
                row.state = "sent"
                row.sent_at = datetime.now(UTC)
                counts["processed"] += 1
            elif row.attempts >= max_attempts:
                row.state = "dead"
                counts["dead_lettered"] += 1
            else:
                row.state = "pending"
                row.retry_at = datetime.now(UTC) + timedelta(seconds=30)
                counts["retried"] += 1
            await session.commit()
    return counts
