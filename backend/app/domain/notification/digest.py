"""摘要 —— 把「你不在时发生的事」合成一封邮件（设计稿「摘要频率」一行）。

## 怎么攒、怎么发

投递那一刻，`outbox` 把「本该发邮件、但此刻发不出去」的那些（安静时段压掉的立即
邮件，以及本来就选了摘要的那一类）记成一行 `delivery_channels`，`channel="digest"`。
那些行就是这份摘要的内容，静静躺在那里。

`run_notification_digests` 是唯一的发信口。对每个还有待发摘要的人：按他自己的
`digest_cadence` 算一个周期（每天 / 每周），**最早那一行**攒够了这个周期才发一封，
发出去的一封把这个人当前所有待发摘要一起带走。于是「每周」就是每周最多一封，而
一封信里是他这一周漏掉的全部 —— 不用再单独记「上次摘要发在什么时候」，攒的行自己
就是那个时钟。

## 谁来按铃

它是个定时任务，不是进程内的闹钟：一封邮件按天、按周发，不需要谁一直醒着。仓库里
的钟在 `app.core.background.periodic_jobs`（`notification_digest_interval_s`，默认
每小时看一次，每次只处理攒够周期的人）；同一件事也可以直接由 cron 调这个入口：

    python -c "import anyio; from app.core.db import SessionFactory; \\
from app.domain.notification.digest import run_notification_digests; \\
anyio.run(run_notification_digests, SessionFactory)"

## 认领与重试

和 `outbox.drain_channel` 同一套：认领时把行从 `pending` 改成 `sending` 并记一段
租约（`lease_until`），认领期间别的进程抢不走；发送成功落 `sent`，失败落回 `pending`
并记一次重试。进程在中间没了，租约到期后下一轮会把行重新捡起来 —— 不会因为一次
崩溃就永远沉在一封发不出去的摘要里。
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from app.core.db import SessionFactory
from app.core.email import get_email_sender, is_placeholder_email
from app.domain.delivery.models import ChannelDelivery
from app.domain.notification.maintenance import email_link, headline_for
from app.domain.notification.outbox import LEASE_SECONDS
from app.domain.notification.preferences import DigestCadence, EmailMode
from app.domain.notification.preferences_models import PreferencesRepository
from app.domain.user.models import User

logger = logging.getLogger(__name__)

#: 每种频率攒多久才发一封。`off` 不在这里 —— 它连摘要行都不会有。
_PERIOD: dict[DigestCadence, timedelta] = {
    DigestCadence.daily: timedelta(days=1),
    DigestCadence.weekly: timedelta(days=7),
}


def compose_digest(items: list[dict[str, Any]]) -> tuple[str, str]:
    """(标题, HTML 正文)。一条一行，每行都指向它说的那件事。"""
    subject = f"[芝士] 你不在时的 {len(items)} 条通知"
    body = ["<p>你不在的时候，芝士上有这些事：</p><ul>"]
    for item in items:
        raw = item.get("payload")
        payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        headline = html.escape(headline_for(str(payload.get("type") or "")))
        link = html.escape(email_link(payload), quote=True)
        body.append(f'<li><a href="{link}">{headline}</a></li>')
    body.append("</ul>")
    return subject, "".join(body)


async def send_digest_email(
    sessions: SessionFactory, receiver_id: int, items: list[dict[str, Any]]
) -> bool:
    async with sessions() as session:
        email = await session.scalar(select(User.email).where(User.id == receiver_id))
    if not email or is_placeholder_email(email):
        logger.warning("摘要收件人 %s 没有可用邮箱", receiver_id)
        return False
    subject, body_html = compose_digest(items)
    try:
        return bool(
            await get_email_sender().send(
                to=email, subject=subject, body_html=body_html
            )
        )
    except Exception:
        logger.exception("摘要发送失败 receiver=%s", receiver_id)
        return False


async def _claim_due_digest(
    sessions: SessionFactory, receiver_id: int, *, now: datetime
) -> tuple[uuid.UUID, list[dict[str, Any]]] | None:
    """这个人的摘要攒够他的周期了吗？够了就认领，返回 (令牌, 内容)；没够返回 None。"""
    stamp = datetime.now(UTC)
    async with sessions() as session:
        pref = await PreferencesRepository(session).for_user(receiver_id)
        period = _PERIOD.get(pref.digest_cadence)
        # 关掉了摘要、或把邮件整个关掉了：这一轮不带他的。
        if period is None or pref.email_mode is EmailMode.off:
            return None
        rows = list(
            await session.scalars(
                select(ChannelDelivery)
                .where(
                    ChannelDelivery.channel == "digest",
                    ChannelDelivery.receiver_id == receiver_id,
                    ChannelDelivery.state.in_(("pending", "sending")),
                    or_(
                        ChannelDelivery.lease_until.is_(None),
                        ChannelDelivery.lease_until <= stamp,
                    ),
                )
                .order_by(ChannelDelivery.recorded_at)
                .with_for_update(skip_locked=True)
            )
        )
        if not rows:
            return None
        # 攒够一个周期才发（`rows` 按时间排过，最早那一行说了算）。
        if rows[0].recorded_at > now - period:
            return None
        token = uuid.uuid4()
        for row in rows:
            row.state = "sending"
            row.claim_token = token
            row.attempts += 1
            row.lease_until = stamp + timedelta(seconds=LEASE_SECONDS)
        items = [dict(row.payload or {}) for row in rows]
        await session.commit()
    return token, items


async def _finish_digest(
    sessions: SessionFactory, token: uuid.UUID, *, delivered: bool
) -> None:
    """把认领过的那一批落定：成功 `sent`，失败放回 `pending` 等下一轮。"""
    async with sessions() as session:
        rows = await session.scalars(
            select(ChannelDelivery).where(
                ChannelDelivery.claim_token == token,
                ChannelDelivery.state == "sending",
            )
        )
        for row in rows:
            row.lease_until = None
            if delivered:
                row.state = "sent"
                row.sent_at = datetime.now(UTC)
            else:
                row.state = "pending"
                row.retry_at = datetime.now(UTC) + timedelta(seconds=30)
        await session.commit()


async def run_notification_digests(
    sessions: SessionFactory, *, now: datetime | None = None
) -> dict[str, int]:
    """给每个攒够周期的人发一封摘要。返回这一轮发了几封、带了几条。"""
    now = now or datetime.now(UTC)
    counts = {"digests": 0, "items": 0}
    async with sessions() as session:
        receivers = list(
            await session.scalars(
                select(ChannelDelivery.receiver_id)
                .where(
                    ChannelDelivery.channel == "digest",
                    ChannelDelivery.state.in_(("pending", "sending")),
                )
                .distinct()
            )
        )
    for receiver_id in receivers:
        claimed = await _claim_due_digest(sessions, receiver_id, now=now)
        if claimed is None:
            continue
        token, items = claimed
        delivered = await send_digest_email(sessions, receiver_id, items)
        await _finish_digest(sessions, token, delivered=delivered)
        if delivered:
            counts["digests"] += 1
            counts["items"] += len(items)
    return counts


__all__ = [
    "compose_digest",
    "run_notification_digests",
    "send_digest_email",
]
