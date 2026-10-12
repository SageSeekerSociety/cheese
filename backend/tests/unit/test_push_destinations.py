"""A system notification returns its reader to the conversation and message."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.notification import push_delivery
from app.domain.notification.push import push_link


@pytest.mark.parametrize(
    ("where", "path"),
    [
        ({}, "/projects/p/topics/c"),
        ({"taskId": "t"}, "/projects/p/topics/c/tasks/t"),
        ({"threadId": "s"}, "/projects/p/topics/c/threads/s"),
    ],
)
def test_system_notification_links_open_at_the_message(where, path):
    payload = {"projectId": "p", "topicId": "c", **where}
    assert push_link(payload) == path
    assert push_link({**payload, "blockId": "m"}) == f"{path}?block=m"


def test_a_notice_without_a_conversation_returns_to_the_inbox():
    assert push_link({"projectId": "p", "taskId": "t", "blockId": "m"}) == "/inbox"
    assert push_link({}) == "/inbox"


@pytest.mark.parametrize("where", [{"taskId": "t"}, {"threadId": "s"}, {}])
async def test_browser_push_preserves_the_destination_sent_by_the_event(
    monkeypatch, where
):
    @asynccontextmanager
    async def sessions():
        yield object()

    class Subscriptions:
        def __init__(self, session):
            pass

        async def for_user(self, user_id):
            assert user_id == 12
            return [
                SimpleNamespace(
                    endpoint="https://push.example/sub", p256dh="key", auth="auth"
                )
            ]

    sent = []

    def send_one(**kwargs):
        sent.append(json.loads(kwargs["body"]))
        return 201

    monkeypatch.setattr(push_delivery, "PushSubscriptionRepository", Subscriptions)
    monkeypatch.setattr(push_delivery, "_send_one", send_one)
    target = {"projectId": "p", "topicId": "c", "blockId": "m", **where}
    await push_delivery.send_push(
        sessions,
        {"recipientId": 12, "title": "Ready", "body": "Review", **target},
    )
    assert len(sent) == 1
    assert sent[0].items() >= target.items()
    assert "recipientId" not in sent[0]
