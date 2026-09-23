"""Committed browser-push intents with bounded retries and expired endpoint cleanup."""

from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.notification.push import PushSubscriptionRepository

logger = logging.getLogger(__name__)

#: 推送服务商说这个订阅已经不存在了。删，不重试。
_GONE = (404, 410)


def _send_one(*, endpoint: str, p256dh: str, auth: str, body: str) -> int:
    """同步地发一条，返回 HTTP 状态码。抛异常交给调用方当失败处理。

    `pywebpush` 是同步库（内部用 requests），所以调用方把它丢进线程里 —— 一次投递
    要做一次椭圆曲线加密再打一次 HTTP，放在事件循环上会把整个后端卡住。
    """
    from pywebpush import webpush

    response = webpush(
        subscription_info={
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        },
        data=body,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        # Use a positive TTL for WNS compatibility; notices expire after five minutes.
        ttl=300,
        timeout=10,
    )
    return int(getattr(response, "status_code", 201))


def _status_of(exc: Exception) -> int:
    """从 pywebpush 抛出来的异常里挖出 HTTP 状态码，挖不到返回 0。

    它把 requests 的响应挂在 `WebPushException.response` 上。挖不到就当成一次普通
    失败走重试 —— 唯一要区分出来的是「订阅没了」，而那个一定带着 404/410。
    """
    response = getattr(exc, "response", None)
    return int(getattr(response, "status_code", 0) or 0)


async def send_push(sessions, item):
    import asyncio

    async with sessions() as session:
        subscriptions = await PushSubscriptionRepository(session).for_user(
            int(item["recipientId"])
        )
        targets = [(r.endpoint, r.p256dh, r.auth) for r in subscriptions]
    body = json.dumps(
        {key: item.get(key) for key in ("title", "body", "projectId", "topicId")},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    gone, problems = [], []
    for endpoint, p256dh, auth in targets:
        try:
            status = await asyncio.to_thread(
                _send_one, endpoint=endpoint, p256dh=p256dh, auth=auth, body=body
            )
        except Exception as exc:
            status = _status_of(exc)
            if status not in _GONE:
                problems.append(str(exc))
                continue
        if status in _GONE:
            gone.append(endpoint)
        elif status >= 400:
            problems.append(f"HTTP {status}")
    if gone:
        async with sessions() as session:
            repo = PushSubscriptionRepository(session)
            for endpoint in gone:
                await repo.drop(endpoint)
            await session.commit()
    # User-channel success is retained: one failed subscription must not resend
    # to browsers that accepted the same notification.
    if problems and len(problems) == len(targets):
        raise RuntimeError("; ".join(problems)[:1000])
    return len(gone)


async def drain_push_queue(sessions: SessionFactory) -> dict[str, int]:
    from app.domain.notification.legacy_queue import import_legacy_queue
    from app.domain.notification.outbox import drain_channel

    try:
        await import_legacy_queue(
            sessions, channel="push", batch_size=settings.notification_push_batch_size
        )
    except Exception:
        # Redis migration failure must not stall new SQL-backed notifications.
        logger.exception("Legacy push queue migration will retry next tick")

    if not settings.web_push_configured:
        return {"processed": 0, "retried": 0, "dead_lettered": 0, "expired": 0}
    return await drain_channel(
        sessions,
        channel="push",
        batch_size=settings.notification_push_batch_size,
        max_attempts=max(1, settings.notification_push_max_retries),
    )
