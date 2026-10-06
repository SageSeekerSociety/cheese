"""摘要的发送口 run_notification_digests —— 攒够周期才发，发不出去就放回去。

真正的行、真正的用户名册、真正的认领；只有外部发送那一端被换成一个记账的信箱。
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.domain.delivery.models import ChannelDelivery
from app.domain.notification import digest
from app.domain.notification.models import NotificationType
from app.domain.notification.preferences import (
    DigestCadence,
    EmailMode,
    default_preferences,
)
from app.domain.notification.preferences_models import PreferencesRepository
from app.domain.user.repositories import UserRepository


def _install_sender(monkeypatch, *, delivers: bool = True):
    box = SimpleNamespace(send=AsyncMock(return_value=delivers))
    monkeypatch.setattr(digest, "get_email_sender", lambda: box)
    return box


async def _make_user(db_factory, handle: str) -> int:
    async with db_factory() as session:
        user = await UserRepository(session).create_user(
            username=handle, email=f"{handle}@example.com"
        )
        await session.commit()
        return user.id


async def _set_pref(db_factory, user_id: int, **overrides) -> None:
    async with db_factory() as session:
        pref = replace(default_preferences(), **overrides)
        await PreferencesRepository(session).save(user_id, pref)
        await session.commit()


async def _add_digest_row(
    db_factory, user_id: int, *, key: str, recorded_at: datetime
) -> None:
    async with db_factory() as session:
        session.add(
            ChannelDelivery(
                id=uuid.uuid4(),
                delivery_key=key,
                channel="digest",
                receiver_id=user_id,
                payload={
                    "type": NotificationType.MENTION.value,
                    "payload": {
                        "projectId": "22222222-2222-2222-2222-222222222222",
                        "topicId": "11111111-1111-1111-1111-111111111111",
                        "content": "look here",
                    },
                },
                recorded_at=recorded_at,
                state="pending",
            )
        )
        await session.commit()


async def _rows(db_factory, user_id: int) -> list[ChannelDelivery]:
    async with db_factory() as session:
        return list(
            await session.scalars(
                select(ChannelDelivery).where(ChannelDelivery.receiver_id == user_id)
            )
        )


async def test_a_week_old_pile_becomes_one_email(db_factory, monkeypatch):
    box = _install_sender(monkeypatch)
    user_id = await _make_user(db_factory, "digest-due")
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-a:digest-due",
        recorded_at=datetime.now(UTC) - timedelta(days=8),
    )
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-b:digest-due",
        recorded_at=datetime.now(UTC) - timedelta(days=7),
    )

    counts = await digest.run_notification_digests(db_factory)

    assert counts == {"digests": 1, "items": 2}
    box.send.assert_awaited_once()
    body_html = box.send.call_args.kwargs["body_html"]
    assert "11111111-1111-1111-1111-111111111111" in body_html
    assert all(row.state == "sent" for row in await _rows(db_factory, user_id))


async def test_a_fresh_pile_waits_for_its_period(db_factory, monkeypatch):
    box = _install_sender(monkeypatch)
    user_id = await _make_user(db_factory, "digest-young")
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-c:digest-young",
        recorded_at=datetime.now(UTC) - timedelta(days=1),
    )

    counts = await digest.run_notification_digests(db_factory)

    assert counts == {"digests": 0, "items": 0}
    box.send.assert_not_awaited()
    assert [row.state for row in await _rows(db_factory, user_id)] == ["pending"]


async def test_cadence_off_sends_nothing(db_factory, monkeypatch):
    box = _install_sender(monkeypatch)
    user_id = await _make_user(db_factory, "digest-off")
    await _set_pref(db_factory, user_id, digest_cadence=DigestCadence.off)
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-d:digest-off",
        recorded_at=datetime.now(UTC) - timedelta(days=8),
    )

    assert await digest.run_notification_digests(db_factory) == {
        "digests": 0,
        "items": 0,
    }
    box.send.assert_not_awaited()


async def test_email_off_sends_nothing(db_factory, monkeypatch):
    box = _install_sender(monkeypatch)
    user_id = await _make_user(db_factory, "digest-mail-off")
    await _set_pref(db_factory, user_id, email_mode=EmailMode.off)
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-e:digest-mail-off",
        recorded_at=datetime.now(UTC) - timedelta(days=8),
    )

    assert await digest.run_notification_digests(db_factory) == {
        "digests": 0,
        "items": 0,
    }
    box.send.assert_not_awaited()


async def test_a_failed_send_puts_the_lines_back(db_factory, monkeypatch):
    box = _install_sender(monkeypatch, delivers=False)
    user_id = await _make_user(db_factory, "digest-retry")
    await _add_digest_row(
        db_factory,
        user_id,
        key="mention-f:digest-retry",
        recorded_at=datetime.now(UTC) - timedelta(days=8),
    )

    assert await digest.run_notification_digests(db_factory) == {
        "digests": 0,
        "items": 0,
    }
    box.send.assert_awaited_once()
    assert [row.state for row in await _rows(db_factory, user_id)] == ["pending"]
