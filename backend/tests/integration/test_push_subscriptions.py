"""浏览器推送的订阅，和「哪些通知配得上一条推送」。

推送和站内通知、邮件的区别在于它会打断人。所以这里钉两件事：

- **订阅按浏览器去重**：同一个浏览器反复订阅（重新授权、service worker 换代、清了
  站点数据又装回来）换回同一个 endpoint，不能越攒越多；而两把加密材料可能变了，所
  以要覆盖 —— 拿旧钥匙加密的内容新浏览器解不开，症状是推送静默地不出现。
- **只有「下一步在人手上」那两种会推**。芝士跑一轮产生几十条消息，按消息推送就是几
  十条推送，人会直接关掉这个渠道，之后真正要他动手的那一条也收不到了。
"""

import pytest

from app.domain.notification.handlers import NotificationDelivery
from app.domain.notification.models import NotificationType
from app.domain.notification.push import (
    PUSHABLE,
    RedisPushQueueNotificationHandler,
    push_text,
)
from app.domain.notification.push_models import PushSubscription
from tests.conftest import seed_user

_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _save(client, token: str, *, endpoint: str = _ENDPOINT, p256dh: str = "key-1"):
    return client.put(
        "/push/subscriptions",
        json={"endpoint": endpoint, "p256dh": p256dh, "auth": "auth-1"},
        headers=_headers(token),
    )


def _rows(client) -> list[PushSubscription]:
    async def go():
        async with client.test_factory() as session:
            from sqlalchemy import select

            return list((await session.scalars(select(PushSubscription))).all())

    return client.portal.call(go)


def test_a_browser_that_subscribes_twice_is_still_one_subscription(client):
    token = seed_user(client, "alice")

    assert _save(client, token).status_code == 200
    assert _save(client, token, p256dh="key-2").status_code == 200

    (row,) = _rows(client)
    assert row.endpoint == _ENDPOINT
    # 覆盖而不是忽略：旧钥匙加密的内容新浏览器解不开。
    assert row.p256dh == "key-2"


def test_two_browsers_are_two_subscriptions(client):
    token = seed_user(client, "alice")

    _save(client, token)
    _save(
        client, token, endpoint="https://updates.push.services.mozilla.com/wpush/v2/xyz"
    )

    assert len(_rows(client)) == 2


def test_only_the_owner_can_drop_a_subscription(client):
    """endpoint 唯一，但它不是秘密 —— 只按它删等于谁都能替别人关掉推送。"""
    alice = seed_user(client, "alice")
    bob = seed_user(client, "bob")
    _save(client, alice)

    r = client.post(
        "/push/subscriptions/delete",
        json={"endpoint": _ENDPOINT},
        headers=_headers(bob),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted"] is False
    assert len(_rows(client)) == 1

    r = client.post(
        "/push/subscriptions/delete",
        json={"endpoint": _ENDPOINT},
        headers=_headers(alice),
    )
    assert r.json()["data"]["deleted"] is True
    assert _rows(client) == []


def test_a_deployment_without_keys_says_so_instead_of_failing(client):
    """没配 VAPID 密钥时如实说不可用。

    前端拿到 `available: false` 就不去问权限 —— 问了也没有东西能发，而浏览器的推送
    权限被拒一次之后很难再问第二次。
    """
    r = client.get("/push/key")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["available"] is False
    assert data["key"] is None


def test_a_deployment_with_keys_hands_out_the_public_one(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "vapid_public_key", "public-key", raising=False)
    monkeypatch.setattr(settings, "vapid_private_key", "private-key", raising=False)

    data = client.get("/push/key").json()["data"]
    assert data == {"available": True, "key": "public-key"}


class _FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[str] = []

    async def rpush(self, _key: str, *items: str) -> int:
        self.pushed.extend(items)
        return len(self.pushed)


@pytest.mark.anyio
async def test_only_a_notice_that_needs_a_person_is_worth_a_push():
    """芝士运行过程中的输出一条都不推 —— 那是人关掉这个渠道的头号原因。"""
    redis = _FakeRedis()
    handler = RedisPushQueueNotificationHandler(
        redis,  # type: ignore[arg-type]
        queue_key="q",
    )

    await handler.send_batch(
        [
            NotificationDelivery(
                recipient_id=1,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "content": "验收卡已提交，待 alice 验收",
                    "topicTitle": "预算复核",
                },
            ),
            NotificationDelivery(
                recipient_id=1,
                type=NotificationType.MENTION,
                payload={"content": "有人提到你"},
            ),
        ]
    )

    assert len(redis.pushed) == 1
    assert "验收卡已提交" in redis.pushed[0]
    assert NotificationType.MENTION not in PUSHABLE


def test_the_push_says_the_same_sentence_the_room_says():
    """不另写一套措辞 —— 推送、站内通知、房间里那一行是同一句话。"""
    title, body = push_text(
        NotificationType.ROOM_NOTICE,
        {"content": "验收卡已提交，待 alice 验收", "topicTitle": "预算复核"},
    )
    assert title == "验收卡已提交，待 alice 验收"
    assert body == "在「预算复核」"


def test_a_question_pushes_the_question_itself():
    title, body = push_text(
        NotificationType.CHEESE_QUESTION,
        {"question": "预算按哪个口径统计", "topicTitle": "预算复核"},
    )
    assert title == "预算按哪个口径统计"
    assert body == "在「预算复核」"
