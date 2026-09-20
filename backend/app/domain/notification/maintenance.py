"""The two notification jobs that only a clock can start.

Everything else in this domain runs on the request that caused it: a mention
publishes, a handler writes the in-app row and pushes the email onto Redis.
These two have no such caller.

``finalize_expired_aggregations`` closes an aggregation window. A burst of
mentions is merged into one notification that stays open for
``notification_config.aggregation_window``; the merged notification is only
DELIVERED when that window is finalized, so without this the aggregated ones
are written and never sent — the exact notifications a busy room produces most
of.

``drain_email_queue`` is the only consumer of the Redis list every email
notification is pushed onto. Nothing else reads that key, so an unrun drain is
not a delay: it is a queue that grows forever and an inbox that never receives.
"""

import html
import json
import logging
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.email import get_email_sender
from app.domain.notification.publisher import build_notification_event_handler
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
    if item.get("finalized"):
        # 聚合窗口收口发出的那一条，代表的是一批而不是一件。不说明的话，收件人
        # 会以为平台把其余几十条弄丢了。
        headline += "（这一批合并成了一条）"

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


async def finalize_expired_aggregations(sessions: SessionFactory) -> dict[str, int]:
    """Close every aggregation window that has expired, delivering what it held."""
    async with sessions() as session:
        handler = build_notification_event_handler(session)
        finalized = await handler.finalize_expired()
        await session.commit()
    return {"finalized": len(finalized)}


async def drain_email_queue(sessions: SessionFactory) -> dict[str, int]:
    """Deliver queued email with claim/ack and bounded retry semantics."""
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue_key = settings.notification_email_queue_key
    processing_key = f"{queue_key}:processing"
    dead_letter_key = f"{queue_key}:dead"
    lock_key = f"{queue_key}:consumer-lock"
    batch_size = settings.notification_email_batch_size
    max_retries = max(1, settings.notification_email_max_retries)
    sender = get_email_sender()
    processed = 0
    retried = 0
    dead_lettered = 0
    lock = redis.lock(lock_key, timeout=90)
    lock_acquired = False

    try:
        lock_acquired = await lock.acquire(blocking=False)
        if not lock_acquired:
            logger.info("Email queue consumer is already running")
            return {"processed": 0, "retried": 0, "dead_lettered": 0}

        # A process can die after LMOVE and before acknowledgement. Once its
        # lock expires, the next sole consumer puts those claims back before
        # taking new work. SMTP is therefore at-least-once, never at-most-once.
        while await redis.lmove(processing_key, queue_key, "LEFT", "RIGHT"):
            pass
        batch_count = min(batch_size, await redis.llen(queue_key))

        async with sessions() as session:
            for _ in range(batch_count):
                claimed = await redis.lmove(queue_key, processing_key, "LEFT", "RIGHT")
                if claimed is None:
                    break
                item_str = claimed.decode() if isinstance(claimed, bytes) else claimed

                item: dict[str, Any] = {}
                failure: str | None = None
                try:
                    decoded = json.loads(item_str)
                    if not isinstance(decoded, dict):
                        raise ValueError("email queue payload is not an object")
                    item = decoded
                    recipient_id = item.get("recipientId")
                    if not recipient_id:
                        raise ValueError("email queue payload has no recipientId")

                    stmt = select(User.email).where(User.id == recipient_id)
                    result = await session.execute(stmt)
                    email = result.scalar_one_or_none()
                    if not email:
                        raise ValueError("email recipient has no address")

                    subject, body_html = _compose_email(item)
                    sent = await sender.send(
                        to=email,
                        subject=subject,
                        body_html=body_html,
                    )
                    if not sent:
                        failure = "SMTP delivery returned false"
                except Exception as exc:  # noqa: BLE001 — retain for retry below
                    failure = f"{type(exc).__name__}: {exc}"

                if failure is None:
                    await redis.lrem(processing_key, 1, item_str)
                    processed += 1
                else:
                    retry_count = int(item.get("_emailRetry") or 0) + 1
                    item["_emailRetry"] = retry_count
                    item["_emailError"] = failure[:300]
                    destination = (
                        dead_letter_key if retry_count >= max_retries else queue_key
                    )
                    encoded = json.dumps(item, separators=(",", ":"))
                    pipe = redis.pipeline(transaction=True)
                    pipe.lrem(processing_key, 1, item_str)
                    pipe.rpush(destination, encoded)
                    await pipe.execute()
                    if destination == dead_letter_key:
                        dead_lettered += 1
                        logger.error(
                            "Email queue item moved to dead letter after "
                            "%d attempts: %s",
                            retry_count,
                            failure,
                        )
                    else:
                        retried += 1
                        logger.warning(
                            "Email delivery failed; retained for retry %d/%d: %s",
                            retry_count,
                            max_retries,
                            failure,
                        )
                await lock.extend(90, replace_ttl=True)
    finally:
        if lock_acquired:
            try:
                await lock.release()
            except Exception:  # noqa: BLE001 — TTL still prevents a stuck lock
                logger.exception("Failed to release email queue consumer lock")
        await redis.aclose()

    return {
        "processed": processed,
        "retried": retried,
        "dead_lettered": dead_lettered,
    }
