"""Committed email intents are consumed outside the producer transaction."""

import html
import logging
from typing import Any, Final

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.email import get_email_sender, is_placeholder_email
from app.domain.user.models import User

logger = logging.getLogger(__name__)


#: 通知类型到邮件标题里那句人话。收件人是在自己的邮箱里读到它的，那里没有任何
#: 上下文，所以 `TEAM_REQUEST_APPROVED` 这样的类型代号对他等于乱码。
_SUBJECT_LINES: Final[dict[str, str]] = {
    "MENTION": "有人在芝士里提到了你",
    "REPLY": "有人回复了你",
    "REACTION": "有人对你的内容做了表态",
    "PROJECT_INVITE": "你收到一个项目邀请",
    "DEADLINE_REMIND": "有一个截止时间快到了",
    "TEAM_JOIN_REQUEST": "有人申请加入你的团队",
    "TEAM_INVITATION": "你收到一个团队邀请",
    "TEAM_REQUEST_APPROVED": "你的加入申请通过了",
    "TEAM_REQUEST_REJECTED": "你的加入申请被拒绝了",
    "TEAM_INVITATION_ACCEPTED": "你的团队邀请被接受了",
    "TEAM_INVITATION_DECLINED": "你的团队邀请被谢绝了",
    "TEAM_INVITATION_CANCELED": "一个团队邀请被取消了",
    "TEAM_REQUEST_CANCELED": "一个加入申请被取消了",
}

#: `payload` 的形状按类型各不相同，所以摘要只从这几个常见键里取第一个有字的，
#: 取不到就不放摘要。猜错一个键的代价是邮件少一行；猜整个结构的代价是发错内容。
_SUMMARY_KEYS: Final = ("content", "text", "title", "message", "name")


def _compose_email(item: dict[str, Any]) -> tuple[str, str]:
    """(标题, HTML 正文)。

    这封信要做的事只有一件：让人知道发生了什么、并且能回到平台上去看。所以它
    不解析每种类型的 payload（那需要把 entity resolver 那一套依赖都拖进来），
    只给类型的人话、一段可能有的摘要，和一个链接。

    每一段用户内容都转义过：`payload` 里装的是别人写的字，而这段 HTML 会落进
    某个人的邮件客户端。
    """
    type_ = str(item.get("type") or "")
    headline = _SUBJECT_LINES.get(type_, "你在芝士上有一条新通知")

    summary = ""
    payload = item.get("payload")
    if isinstance(payload, dict):
        for key in _SUMMARY_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                summary = value.strip()[:200]
                break

    link = settings.frontend_url
    body = [f"<p>{html.escape(headline)}</p>"]
    if summary:
        body.append(f"<blockquote>{html.escape(summary)}</blockquote>")
    body.append(f'<p><a href="{html.escape(link, quote=True)}">到芝士里查看</a></p>')
    return f"[芝士] {headline}", "".join(body)


async def send_email(sessions, item):
    async with sessions() as session:
        email = await session.scalar(
            select(User.email).where(User.id == item["recipientId"])
        )
    if not email or is_placeholder_email(email):
        raise ValueError("Email recipient has no address")
    subject, body_html = _compose_email(item)
    if not await get_email_sender().send(
        to=email, subject=subject, body_html=body_html
    ):
        raise RuntimeError("SMTP delivery returned false")


async def drain_email_queue(sessions: SessionFactory) -> dict[str, int]:
    from app.domain.notification.legacy_queue import import_legacy_queue
    from app.domain.notification.outbox import drain_channel

    try:
        await import_legacy_queue(
            sessions, channel="email", batch_size=settings.notification_email_batch_size
        )
    except Exception:
        # Redis migration failure must not stall new SQL-backed notifications.
        logger.exception("Legacy email queue migration will retry next tick")

    result = await drain_channel(
        sessions,
        channel="email",
        batch_size=settings.notification_email_batch_size,
        max_attempts=max(1, settings.notification_email_max_retries),
    )
    result.pop("expired")
    return result
