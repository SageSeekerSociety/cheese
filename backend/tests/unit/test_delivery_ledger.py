"""投递账本：这一批没算送到的两档上各出一次事，收件人手里仍然恰好一条（结论 58）。

留下「账本里有行、`sent_at` 是 NULL」的不是崩溃 —— 那一行、收件箱那一行和 `sent_at`
的回写在同一个事务里，提交之前没了就一起回滚。真正会留下它的是**事务照常提交而
`dispatch()` 返回 False**，两档形状不同、救法也不同：一个渠道都没收下的靠补发；站内
信已经收下、这一批却没算送到的靠去重键。两档在生产里都是无声的 —— 一条通知没到或者
到了两遍，没有任何报错。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.delivery import ledger as ledger_module
from app.domain.delivery.addressing import REASON_REVIEWER, Addressed, Recipient
from app.domain.delivery.ledger import MAX_ATTEMPTS, DeliveryEvent, Ledger
from app.domain.delivery.models import Delivery
from app.domain.notification.handlers import (
    InAppNotificationHandler,
    NotificationDelivery,
    NotificationEventHandler,
)
from app.domain.notification.models import Notification, NotificationType
from app.domain.user.models import User
from tests.support.failing_ledger import FailingLedger, FakeClock

pytestmark = pytest.mark.anyio

EVENT_AT = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


async def _user(session, handle: str) -> int:
    now = datetime.now(UTC)
    user = User(
        username=handle,
        email=f"{handle}@example.invalid",
        hashed_password="x",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    return user.id


def _event(payload: dict | None = None, event_id: uuid.UUID | None = None):
    return DeliveryEvent(
        id=event_id or uuid.uuid4(),
        type=NotificationType.ROOM_NOTICE,
        payload=payload or {"content": "卡递上来了，等你验收"},
        occurred_at=EVENT_AT,
    )


def _addressed(*handles: str) -> Addressed:
    return Addressed(
        tuple(Recipient(handle=h, reason=REASON_REVIEWER) for h in handles)
    )


async def _inbox(session, receiver_id: int) -> list[Notification]:
    rows = await session.scalars(
        select(Notification)
        .where(Notification.receiver_id == receiver_id)
        .order_by(Notification.id)
    )
    return list(rows)


class _RecordingChannel:
    """数自己被交了几次的渠道 —— 邮件队列和推送队列的替身。

    那两个真渠道都是往 Redis 里 rpush，队列里没有去重键：交第二遍就是真的多一封
    信、多一条推送。所以「交了几次」正是这里要数的东西。
    """

    name = "recording"

    def __init__(self) -> None:
        self.handed: list[str | None] = []

    async def send_batch(self, deliveries: list[NotificationDelivery]) -> None:
        self.handed.extend(d.delivery_key for d in deliveries)


class _FailingChannel:
    """一个怎么都收不下的渠道。"""

    name = "failing"

    async def send_batch(self, deliveries: list[NotificationDelivery]) -> None:
        raise RuntimeError("这个渠道收不下")


async def _ledger_rows(session) -> list[Delivery]:
    rows = await session.scalars(select(Delivery).order_by(Delivery.recorded_at))
    return list(rows)


async def test_a_delivery_no_channel_took_is_resent_once(db_factory):
    """①渠道没全收下：补发把它补出去，而且只补一次。

    以前这一档是静默丢失 —— 渠道那边抛的异常被吞掉，没有任何一行记着这条通知本该
    发出去，所以没有人能再把它发出去。
    """
    clock = FakeClock(EVENT_AT)
    event = _event()

    async with db_factory() as session:
        alice = await _user(session, "alice")
        ledger = FailingLedger(session, now=clock, channels_refuse=True)
        await ledger.deliver(event, _addressed("alice"))
        await session.commit()

        assert await _inbox(session, alice) == []
        (row,) = await _ledger_rows(session)
        assert row.sent_at is None  # 记下来了，没送到

    clock.advance(seconds=3600)
    async with db_factory() as session:  # 下一轮补发
        assert await Ledger(session, now=clock).resend_unsent() == 1
        await session.commit()
        assert len(await _inbox(session, alice)) == 1

    async with db_factory() as session:  # 再扫一遍，不该再发
        assert await Ledger(session, now=clock).resend_unsent() == 0
        await session.commit()
        assert len(await _inbox(session, alice)) == 1


async def test_a_delivery_the_mailbox_took_is_not_sent_twice(db_factory):
    """②站内信收下了、这一批却没算送到：补发不能让他收到第二条。

    分辨不出来的正是这一档 —— 「站内信已经写进去了、只是这一批没算数」和「根本没
    发」在账本里长得一模一样。所以恰好一次由去重键保证，不由发送顺序保证：这里删掉
    `notification.delivery_key` 的唯一约束，收件箱里立刻变成两条。
    """
    clock = FakeClock(EVENT_AT)
    event = _event()

    async with db_factory() as session:
        alice = await _user(session, "alice")
        await FailingLedger(session, now=clock, not_counted_as_sent=True).deliver(
            event, _addressed("alice")
        )
        await session.commit()

        assert len(await _inbox(session, alice)) == 1
        (row,) = await _ledger_rows(session)
        assert row.sent_at is None

    clock.advance(seconds=3600)
    async with db_factory() as session:  # 下一轮补发
        await Ledger(session, now=clock).resend_unsent()
        await session.commit()

        assert len(await _inbox(session, alice)) == 1  # 仍然只有一条
        (row,) = await _ledger_rows(session)
        assert row.sent_at is not None  # 这一次确认回写上了


async def test_the_same_event_counted_twice_reaches_each_person_once(db_factory):
    """③同一条事件被算两遍 —— 每个收件人仍然只收到一次。"""
    clock = FakeClock(EVENT_AT)
    event = _event()

    async with db_factory() as session:
        alice = await _user(session, "alice")
        bob = await _user(session, "bob")
        ledger = Ledger(session, now=clock)

        await ledger.deliver(event, _addressed("alice", "bob"))
        await ledger.deliver(event, _addressed("alice", "bob"))
        await session.commit()

        assert len(await _inbox(session, alice)) == 1
        assert len(await _inbox(session, bob)) == 1
        assert len(await _ledger_rows(session)) == 2


async def test_the_dedup_key_follows_the_event_not_the_attempt(db_factory):
    """④去重键跟着事件走，不跟着这一次发送尝试走。

    同一条事件重算一遍，payload 里带上这一次算出来的时刻 —— 内容变了，事件没变。
    键跟内容走的时候，这里会变成两条通知：#1035 那一类「同一件事收到两遍」就是这么
    来的。
    """
    clock = FakeClock(EVENT_AT)
    event_id = uuid.uuid4()

    async with db_factory() as session:
        alice = await _user(session, "alice")
        ledger = Ledger(session, now=clock)

        await ledger.deliver(
            _event({"content": "卡递上来了", "at": "09:00:00"}, event_id),
            _addressed("alice"),
        )
        await ledger.deliver(
            _event({"content": "卡递上来了", "at": "09:00:01"}, event_id),
            _addressed("alice"),
        )
        await session.commit()

        assert len(await _inbox(session, alice)) == 1
        (row,) = await _ledger_rows(session)
        assert row.dedup_key == f"{event_id}:alice"


async def test_a_resend_uses_the_roster_from_when_the_event_happened(db_factory):
    """⑤补发时名册按事件发生的时刻取。

    补发可能发生在几分钟后，也可能在一次重启之后。那时候再按 handle 查一遍名册，
    查到的是**那时候**的名册 —— 而这条事件点的是事情发生时的那个人。
    """
    clock = FakeClock(EVENT_AT)
    event = _event()

    async with db_factory() as session:
        alice_then = await _user(session, "alice")
        await FailingLedger(session, now=clock, channels_refuse=True).deliver(
            event, _addressed("alice")
        )
        await session.commit()

    async with db_factory() as session:
        # 名册变了：这个 handle 现在是另一个人的。
        old = await session.get(User, alice_then)
        old.username = "alice-left"
        alice_now = await _user(session, "alice")
        await session.commit()

    clock.advance(seconds=3600)
    async with db_factory() as session:
        await Ledger(session, now=clock).resend_unsent()
        await session.commit()

        (landed,) = await _inbox(session, alice_then)
        assert await _inbox(session, alice_now) == []
        assert clock.now - EVENT_AT == timedelta(hours=1)
        # 定档的是名册，不是时间戳：收件箱按 `created_at` 倒序翻页，补发出来的这条
        # 若按事件发生的时刻落库，就会插在一小时前的位置 —— 未读数加一，人打开收件
        # 箱却看不到新东西。
        assert landed.created_at > EVENT_AT


async def test_a_resend_does_not_queue_the_email_and_push_again(
    db_factory, monkeypatch
):
    """⑥补发只重投站内信：邮件和推送不会因为补发再排一遍队。

    账本的「恰好一次」只有站内信担得起 —— 去重键落在 `notification.delivery_key`
    的唯一约束上。队列渠道没有这个键，账本替它们重投一次就是收件人真的多收一份，
    而一行发不出去的投递每分钟被扫一次。
    """
    clock = FakeClock(EVENT_AT)
    event = _event()
    queue = _RecordingChannel()

    def _all_channels(session) -> NotificationEventHandler:
        return NotificationEventHandler(
            session=session,
            channel_handlers=[InAppNotificationHandler(session=session), queue],
        )

    monkeypatch.setattr(
        ledger_module, "build_notification_event_handler", _all_channels
    )

    async with db_factory() as session:
        alice = await _user(session, "alice")
        # 站内信收下了，这一批却没算送到 —— 补发一定会再来一次。
        await FailingLedger(session, now=clock, not_counted_as_sent=True).deliver(
            event, _addressed("alice")
        )
        await session.commit()

    assert len(queue.handed) == 1

    clock.advance(seconds=60)
    async with db_factory() as session:
        await Ledger(session, now=clock).resend_unsent()
        await session.commit()
        assert len(await _inbox(session, alice)) == 1

    assert len(queue.handed) == 1  # 队列这边没有第二份


async def test_a_delivery_that_never_goes_out_stops_being_retried(
    db_factory, monkeypatch
):
    """⑦补发有上限：一行始终发不出去的投递不是一台定时机器。

    到顶之后那一行留在账本里 —— `sent_at` 仍是 NULL，`attempts` 到顶，补发不再扫
    它。查得到、能人工看，但不会每分钟再试一遍。
    """
    clock = FakeClock(EVENT_AT)
    event = _event()

    async with db_factory() as session:
        alice = await _user(session, "alice")
        await FailingLedger(session, now=clock, channels_refuse=True).deliver(
            event, _addressed("alice")
        )
        await session.commit()

    monkeypatch.setattr(
        ledger_module, "InAppNotificationHandler", lambda *, session: _FailingChannel()
    )
    for _ in range(MAX_ATTEMPTS):
        clock.advance(seconds=60)
        async with db_factory() as session:
            assert await Ledger(session, now=clock).resend_unsent() == 0
            await session.commit()

    monkeypatch.undo()
    clock.advance(seconds=60)
    async with db_factory() as session:
        assert await Ledger(session, now=clock).resend_unsent() == 0
        await session.commit()
        assert await _inbox(session, alice) == []
        (row,) = await _ledger_rows(session)
        assert row.sent_at is None
        assert row.attempts == MAX_ATTEMPTS
