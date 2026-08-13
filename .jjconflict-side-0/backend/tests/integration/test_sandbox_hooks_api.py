"""POST /sandbox/hooks/{topic_id} — auth + routing into the topic's turn queue."""

import uuid

from app.core.sandbox_auth import SANDBOX_TOKEN
from app.domain.agent.hook_events import hook_router


def test_hook_rejected_without_valid_token(client):
    topic = str(uuid.uuid4())
    r = client.post(
        f"/sandbox/hooks/{topic}",
        json={"hook_event_name": "Stop"},
        headers={"X-Cheese-Token": "wrong"},
    )
    assert r.status_code == 401


def test_hook_accepted_but_undelivered_without_listener(client):
    topic = str(uuid.uuid4())
    r = client.post(
        f"/sandbox/hooks/{topic}",
        json={"hook_event_name": "SessionStart", "session_id": "x"},
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["data"]["delivered"] is False


def test_hook_routed_to_registered_queue(client):
    topic = str(uuid.uuid4())
    queue = hook_router.register(topic)
    try:
        r = client.post(
            f"/sandbox/hooks/{topic}",
            json={"hook_event_name": "MessageDisplay", "delta": "hi"},
            headers={"X-Cheese-Token": SANDBOX_TOKEN},
        )
        assert r.status_code == 200
        assert r.json()["data"]["delivered"] is True
        assert queue.get_nowait()["delta"] == "hi"
    finally:
        hook_router.unregister(topic, queue)


def test_hook_rejects_non_object_body(client):
    topic = str(uuid.uuid4())
    r = client.post(
        f"/sandbox/hooks/{topic}",
        json=["not", "an", "object"],
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    )
    assert r.status_code == 400
