"""投递那一刻按收件人的偏好裁渠道（设计稿「哪一类、走哪个渠道」）。

站内那一列在写 `notification` 表时裁，外部渠道（邮件 / 摘要 / 推送）在写
`delivery_channels` 时裁。这里两条路都过一遍真实的人与真实的行。
"""

from sqlalchemy import select

from app.domain.delivery.models import ChannelDelivery
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
)
from app.domain.notification.models import Notification, NotificationType
from app.domain.notification.outbox import ChannelIntentHandler
from app.domain.notification.preferences import (
    ChannelChoice,
    PreferenceCategory,
    default_preferences,
    with_channel,
)
from app.domain.notification.preferences_models import PreferencesRepository
from app.domain.user.repositories import UserRepository


async def _make_user(db_factory, handle: str) -> int:
    async with db_factory() as session:
        user = await UserRepository(session).create_user(
            username=handle, email=f"{handle}@example.com"
        )
        await session.commit()
        return user.id


async def _set_in_app(db_factory, user_id: int, category, *, on: bool) -> None:
    async with db_factory() as session:
        pref = with_channel(
            default_preferences(),
            category,
            ChannelChoice(in_app=on, push=False, email=False),
        )
        await PreferencesRepository(session).save(user_id, pref)
        await session.commit()


async def _inbox(db_factory, user_id: int) -> list[Notification]:
    async with db_factory() as session:
        return list(
            await session.scalars(
                select(Notification).where(Notification.receiver_id == user_id)
            )
        )


async def test_in_app_off_keeps_that_category_out_of_the_inbox(db_factory):
    user_id = await _make_user(db_factory, "pref-inapp")
    await _set_in_app(db_factory, user_id, PreferenceCategory.MENTION, on=False)

    async with db_factory() as session:
        await InAppNotificationHandler(session).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.MENTION,
                    payload={"actor": {"id": "1", "type": "user"}},
                    delivery_key="mention-1:bob",
                ),
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.REACTION,
                    payload={"actor": {"id": "2", "type": "user"}},
                    delivery_key="reaction-1:bob",
                ),
            ]
        )
        await session.commit()

    rows = await _inbox(db_factory, user_id)
    # 只留下没被关掉的那一类。
    assert [row.type for row in rows] == [NotificationType.REACTION]


async def test_the_default_keeps_everything_in_the_inbox(db_factory):
    user_id = await _make_user(db_factory, "pref-inapp-default")
    async with db_factory() as session:
        await InAppNotificationHandler(session).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.MENTION,
                    payload={"actor": {"id": "1", "type": "user"}},
                    delivery_key="mention-2:bob",
                )
            ]
        )
        await session.commit()
    assert len(await _inbox(db_factory, user_id)) == 1


async def _channels(db_factory, key: str) -> set[str]:
    async with db_factory() as session:
        rows = await session.scalars(
            select(ChannelDelivery).where(ChannelDelivery.delivery_key == key)
        )
        return {row.channel for row in rows}


async def test_a_muted_category_writes_no_external_channel(db_factory):
    """「有人回应了我」默认只有站内：不推送、也不进邮件（更不折进摘要）。"""
    user_id = await _make_user(db_factory, "pref-muted")
    async with db_factory() as session:
        await ChannelIntentHandler(session, push_enabled=True).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.REACTION,
                    payload={"actor": {"id": "1", "type": "user"}},
                    delivery_key="reaction-2:bob",
                )
            ]
        )
        await session.commit()
    assert await _channels(db_factory, "reaction-2:bob") == set()


async def test_a_pushable_type_also_pushes_when_push_is_on(db_factory):
    user_id = await _make_user(db_factory, "pref-push")
    async with db_factory() as session:
        await ChannelIntentHandler(session, push_enabled=True).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.MENTION,
                    payload={
                        "actor": {"id": "1", "type": "user"},
                        "content": "look here",
                    },
                    delivery_key="mention-3:bob",
                )
            ]
        )
        await session.commit()
    assert await _channels(db_factory, "mention-3:bob") == {"digest", "push"}


async def test_master_push_off_leaves_only_the_digest(db_factory):
    user_id = await _make_user(db_factory, "pref-push-off")
    async with db_factory() as session:
        await ChannelIntentHandler(session, push_enabled=False).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.MENTION,
                    payload={"actor": {"id": "1", "type": "user"}},
                    delivery_key="mention-4:bob",
                )
            ]
        )
        await session.commit()
    assert await _channels(db_factory, "mention-4:bob") == {"digest"}
