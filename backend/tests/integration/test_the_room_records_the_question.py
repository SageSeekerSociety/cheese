"""The room keeps a record of what it was asked, and what it answered.

The panel above the composer is the live surface: a question appears there, you
answer it, and it goes away. What the room had no record of was that the
question was ever asked — so scroll-back could not say what 芝士 had been
stopped for, or who unstopped it.
"""

import time

import pytest

from app.domain.agent.remote_control import key
from tests.integration.conftest import session_auth_headers


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr("app.core.tokens._SECRET", "rc-record-test-key-at-least-32b")


@pytest.fixture
def place(client):
    project = client.post(
        "/projects", json={"name": "RC record", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "RC record", "created_by": "alice"},
    ).json()["data"]
    return project["id"], topic["id"]


def _events(worker, uid):
    """A live question is a  event; the pending entry the room
    reads back is that payload."""
    return {
        "worker_epoch": worker["worker_epoch"],
        "events": [
            {
                "payload": {
                    "uuid": uid,
                    "type": "control_request",
                    "request_id": "ask-1",
                    "request": {
                        "subtype": "can_use_tool",
                        "tool_name": "Bash",
                        "input": {"command": "pwd"},
                    },
                }
            }
        ],
    }


def test_the_question_lands_in_the_room_once_and_the_answer_after_it(client, place):
    from app.api.routes.remote_control import store

    project, topic = place

    async def start():
        session = await store().create(
            {"p": project, "t": topic, "exp": int(time.time()) + 3600}, {}
        )
        return session["id"], await store().bridge(session)

    sid, worker = client.portal.call(start)
    headers = {"Authorization": f"Bearer {worker['worker_jwt']}"}
    try:
        first = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers=headers,
            json=_events(worker, "event-1"),
        )
        assert first.status_code == 200, first.text

        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        asked = [b for b in blocks if "Bash" in (b.get("content") or "")]
        assert len(asked) == 1, [b.get("content") for b in blocks]

        # The worker re-sends its pending list with every batch.
        again = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers=headers,
            json=_events(worker, "event-2"),
        )
        assert again.status_code == 200, again.text
        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert len([b for b in blocks if "Bash" in (b.get("content") or "")]) == 1

        answered = client.post(
            f"/topics/{topic}/agent/answer",
            headers=session_auth_headers("alice"),
            json={
                "session_id": sid,
                "request_id": "ask-1",
                "response": {"behavior": "allow"},
            },
        )
        assert answered.status_code == 200, answered.text
        blocks = client.get(f"/topics/{topic}/blocks").json()["data"]["data"]
        assert any("alice 同意了" in (b.get("content") or "") for b in blocks), [
            b.get("content") for b in blocks
        ]
    finally:

        async def cleanup():
            await store().redis.delete(
                key(sid), key(topic, "current"), key(sid, "voiced")
            )

        client.portal.call(cleanup)
