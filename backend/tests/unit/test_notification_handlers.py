"""Unit tests for app.domain.notification.handlers.

Covers transactional inbox delivery.
"""

import pytest
from sqlalchemy import select

from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
)
from app.domain.notification.models import Notification, NotificationType

# ---------------------------------------------------------------------------
# InAppNotificationHandler
# ---------------------------------------------------------------------------


async def _inbox(session, receiver_id: int) -> list[Notification]:
    rows = await session.scalars(
        select(Notification).where(Notification.receiver_id == receiver_id)
    )
    return list(rows)


class TestInAppNotificationHandler:
    @pytest.mark.anyio
    async def test_send_batch_creates_notifications(self, db_factory):
        async with db_factory() as session:
            await InAppNotificationHandler(session).send_batch(
                [
                    NotificationDelivery(
                        recipient_id=10,
                        type=NotificationType.MENTION,
                        payload={"actor": {"id": "1", "type": "user"}},
                        delivery_key="mention-1:bob",
                    ),
                    NotificationDelivery(
                        recipient_id=20,
                        type=NotificationType.REPLY,
                        payload={"actor": {"id": "2", "type": "user"}},
                        delivery_key="reply-1:carol",
                    ),
                ]
            )

            (mention,) = await _inbox(session, 10)
            (reply,) = await _inbox(session, 20)
            assert mention.type == NotificationType.MENTION
            assert mention.metadata_payload == {"actor": {"id": "1", "type": "user"}}
            assert reply.type == NotificationType.REPLY

    @pytest.mark.anyio
    async def test_the_same_delivery_key_lands_once(self, db_factory):
        """同一笔投递发两遍 —— 收件人手里只有一条。

        这是「发出之后、回写确认之前崩掉」那一档的唯一保障：补发会把同一笔再发一
        遍，而账本那一行看起来仍然没发出去。
        """
        async with db_factory() as session:
            handler = InAppNotificationHandler(session)
            delivery = NotificationDelivery(
                recipient_id=10,
                type=NotificationType.ROOM_NOTICE,
                payload={"content": "卡递上来了"},
                delivery_key="event-1:alice",
            )

            await handler.send_batch([delivery])
            await handler.send_batch([delivery])

            assert len(await _inbox(session, 10)) == 1
