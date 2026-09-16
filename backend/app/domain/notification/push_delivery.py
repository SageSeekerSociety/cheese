"""把排好队的推送真的发出去 —— `drain_push_queue` 是那个队列唯一的消费者。

和 `drain_email_queue` 同一个形状（claim/ack、有界重试、死信、单消费者锁），因为它
们的问题是同一个：投递要打外面的 HTTP 接口，慢和失败都是常态，而产生通知的那个请
求不能等在上面。

一件它和邮件不一样的事：**订阅会自己死掉**。用户在浏览器设置里撤掉权限、清了站点
数据、换了设备，推送服务商就回 404 或 410。那不是「重试一下就好」，那是这一行该删
掉 —— 留着它每一轮都白发一次，而且永远不会成功。所以这两个状态码单独处理，不进重
试、不进死信，直接删订阅。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

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
        timeout=10,
    )
    return int(getattr(response, "status_code", 201))


async def drain_push_queue(sessions: SessionFactory) -> dict[str, int]:
    """Deliver queued browser pushes with claim/ack and bounded retry."""
    import asyncio

    if not settings.web_push_configured:
        # 没配密钥 = 这个部署没开这个渠道，那么队列里也不会有东西（发布侧同样按这
        # 个判断挂不挂渠道）。照样返回一份计数，让调度器的日志一致。
        return {"processed": 0, "retried": 0, "dead_lettered": 0, "expired": 0}

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue_key = settings.notification_push_queue_key
    processing_key = f"{queue_key}:processing"
    dead_letter_key = f"{queue_key}:dead"
    lock_key = f"{queue_key}:consumer-lock"
    max_retries = max(1, settings.notification_push_max_retries)
    processed = retried = dead_lettered = expired = 0
    lock = redis.lock(lock_key, timeout=90)
    lock_acquired = False

    try:
        lock_acquired = await lock.acquire(blocking=False)
        if not lock_acquired:
            logger.info("push queue consumer is already running")
            return {"processed": 0, "retried": 0, "dead_lettered": 0, "expired": 0}

        # 一个进程可能在 LMOVE 之后、确认之前死掉。锁一过期，下一个唯一消费者先把
        # 那些声明放回去再接新活 —— 于是投递是至少一次，而不是至多一次。
        while await redis.lmove(processing_key, queue_key, "LEFT", "RIGHT"):
            pass
        batch_count = min(
            settings.notification_push_batch_size, await redis.llen(queue_key)
        )

        async with sessions() as session:
            subscriptions = PushSubscriptionRepository(session)
            for _ in range(batch_count):
                claimed = await redis.lmove(queue_key, processing_key, "LEFT", "RIGHT")
                if claimed is None:
                    break
                item_str = claimed.decode() if isinstance(claimed, bytes) else claimed

                item: dict[str, Any] = {}
                failure: str | None = None
                gone: list[str] = []
                try:
                    decoded = json.loads(item_str)
                    if not isinstance(decoded, dict):
                        raise ValueError("push queue payload is not an object")
                    item = decoded
                    recipient_id = item.get("recipientId")
                    if not recipient_id:
                        raise ValueError("push queue payload has no recipientId")

                    rows = await subscriptions.for_user(int(recipient_id))
                    if not rows:
                        # 这个人没有任何浏览器开着推送。不是失败 —— 这一条到此为止。
                        rows = []
                    body = json.dumps(
                        {
                            "title": item.get("title") or "",
                            "body": item.get("body") or "",
                            "projectId": item.get("projectId"),
                            "topicId": item.get("topicId"),
                        },
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    problems: list[str] = []
                    for row in rows:
                        try:
                            status = await asyncio.to_thread(
                                _send_one,
                                endpoint=row.endpoint,
                                p256dh=row.p256dh,
                                auth=row.auth,
                                body=body,
                            )
                        except Exception as exc:  # noqa: BLE001 — 见下面的分流
                            status = _status_of(exc)
                            if status in _GONE:
                                gone.append(row.endpoint)
                                continue
                            problems.append(f"{row.endpoint[-12:]}: {exc}")
                            continue
                        if status in _GONE:
                            gone.append(row.endpoint)
                        elif status >= 400:
                            problems.append(f"{row.endpoint[-12:]}: HTTP {status}")
                    # 一个人有几个浏览器，其中一个失败不该让另外几个重发。所以这
                    # 一条只在**全部**都失败时才算失败。
                    if problems and len(problems) == len(rows):
                        failure = "; ".join(problems)[:300]
                except Exception as exc:  # noqa: BLE001 — retain for retry below
                    failure = f"{type(exc).__name__}: {exc}"

                for endpoint in gone:
                    await subscriptions.drop(endpoint)
                    expired += 1
                if gone:
                    await session.commit()

                if failure is None:
                    await redis.lrem(processing_key, 1, item_str)
                    processed += 1
                else:
                    retry_count = int(item.get("_pushRetry") or 0) + 1
                    item["_pushRetry"] = retry_count
                    item["_pushError"] = failure
                    destination = (
                        dead_letter_key if retry_count >= max_retries else queue_key
                    )
                    encoded = json.dumps(
                        item, separators=(",", ":"), ensure_ascii=False
                    )
                    pipe = redis.pipeline(transaction=True)
                    pipe.lrem(processing_key, 1, item_str)
                    pipe.rpush(destination, encoded)
                    await pipe.execute()
                    if destination == dead_letter_key:
                        dead_lettered += 1
                        logger.error(
                            "push moved to dead letter after %d attempts: %s",
                            retry_count,
                            failure,
                        )
                    else:
                        retried += 1
                        logger.warning(
                            "push delivery failed; retained for retry %d/%d: %s",
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
                logger.exception("failed to release push queue consumer lock")
        await redis.aclose()

    return {
        "processed": processed,
        "retried": retried,
        "dead_lettered": dead_lettered,
        "expired": expired,
    }


def _status_of(exc: Exception) -> int:
    """从 pywebpush 抛出来的异常里挖出 HTTP 状态码，挖不到返回 0。

    它把 requests 的响应挂在 `WebPushException.response` 上。挖不到就当成一次普通
    失败走重试 —— 唯一要区分出来的是「订阅没了」，而那个一定带着 404/410。
    """
    response = getattr(exc, "response", None)
    return int(getattr(response, "status_code", 0) or 0)
