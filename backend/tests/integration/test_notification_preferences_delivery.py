"""投递那一刻按收件人的偏好裁渠道（设计稿「哪一类、走哪个渠道」）。

站内那一列在写 `notification` 表时裁，外部渠道（邮件 / 摘要 / 推送）在写
`delivery_channels` 时裁。这里两条路都过一遍真实的人与真实的行。
"""

from datetime import UTC, datetime

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
from app.domain.user.services import set_timezone

#: 记意图的那一刻：北京时间正午，不在默认的安静时段（22:00–08:00）里。这些用例试
#: 的是渠道本身，不该因为跑在夜里就看不到推送和立即邮件。
NOON = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)


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
        await ChannelIntentHandler(
            session, push_enabled=True, now=lambda: NOON
        ).send_batch(
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
        await ChannelIntentHandler(
            session, push_enabled=True, now=lambda: NOON
        ).send_batch(
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
        await ChannelIntentHandler(
            session, push_enabled=False, now=lambda: NOON
        ).send_batch(
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


async def _mentioned_at(db_factory, user_id: int, key: str, moment: datetime) -> None:
    async with db_factory() as session:
        await ChannelIntentHandler(
            session, push_enabled=True, now=lambda: moment
        ).send_batch(
            [
                NotificationDelivery(
                    recipient_id=user_id,
                    type=NotificationType.MENTION,
                    payload={"content": "look here"},
                    delivery_key=key,
                )
            ]
        )
        await session.commit()


#: UTC 15:00：北京时间 23:00（安静时段里），伦敦 16:00（不在）。
AFTERNOON_IN_LONDON = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)


async def test_quiet_hours_run_on_the_recipients_own_clock(db_factory):
    """同一刻，北京的人在夜里、伦敦的人在下午：只有伦敦那个人收到推送。"""
    beijing = await _make_user(db_factory, "tz-beijing")
    london = await _make_user(db_factory, "tz-london")
    async with db_factory() as session:
        await set_timezone(session, beijing, "Asia/Shanghai")
        await set_timezone(session, london, "Europe/London")
        await session.commit()

    await _mentioned_at(db_factory, beijing, "tz-1:beijing", AFTERNOON_IN_LONDON)
    await _mentioned_at(db_factory, london, "tz-1:london", AFTERNOON_IN_LONDON)

    assert await _channels(db_factory, "tz-1:beijing") == {"digest"}
    assert await _channels(db_factory, "tz-1:london") == {"digest", "push"}


async def test_without_a_time_zone_quiet_hours_run_on_beijing_time(db_factory):
    """没报过时区的人按北京时间算，不按 UTC：UTC 15:00 对他是夜里 23:00。"""
    user_id = await _make_user(db_factory, "tz-unknown")
    await _mentioned_at(db_factory, user_id, "tz-2:unknown", AFTERNOON_IN_LONDON)
    assert await _channels(db_factory, "tz-2:unknown") == {"digest"}
