"""The room hears about its session state instead of asking for it every two seconds.

The panel in every open room shows one thing: the question the agent is waiting
on an answer to. That fact lives in the platform's own store, so the process that
takes the worker's event can say so on the socket the room already holds, and the
page can stop asking. Measured on dev before this: a third of all HTTP requests
were this poll, nearly all of them answered "still nothing".
"""

import time

import pytest

from app.domain.agent.remote_control import key


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr("app.core.tokens._SECRET", "rc-push-test-key-at-least-32-bytes")


@pytest.fixture
def place(client):
    project = client.post(
        "/projects", json={"name": "RC push", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "RC push", "created_by": "alice"},
    ).json()["data"]
    return project["id"], topic["id"]


def test_a_question_from_the_agent_reaches_the_room_without_being_polled_for(
    client, place
):
    from app.api.routes.remote_control import store
    from app.domain.agent.runtime import get_broker

    project, topic = place

    async def start_session():
        session = await store().create(
            {"p": project, "t": topic, "exp": int(time.time()) + 3600}, {}
        )
        return session["id"], await store().bridge(session)

    sid, worker = client.portal.call(start_session)
    # Subscribing is what a browser in this room does; the frames it receives are
    # what this test is about.
    subscription = get_broker().subscribe(str(topic), replay=False)
    queue = client.portal.call(subscription.__aenter__)
    try:
        response = client.post(
            f"/v1/code/sessions/{sid}/worker/events",
            headers={"Authorization": f"Bearer {worker['worker_jwt']}"},
            json={
                "worker_epoch": worker["worker_epoch"],
                "events": [
                    {
                        "payload": {
                            "uuid": "event-1",
                            "type": "control_request",
                            "request_id": "ask-1",
                            "request": {
                                "subtype": "can_use_tool",
                                "input": {"questions": [{"question": "两个都要吗？"}]},
                            },
                        }
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text

        async def drain():
            frames = []
            while not queue.empty():
                frames.append(queue.get_nowait())
            return frames

        frames = client.portal.call(drain)
        control = [f for f in frames if f.get("type") == "agent_control"]
        assert control, f"no control frame among {[f.get('type') for f in frames]}"
        state = control[-1]["state"]
        assert state["id"] == sid
        assert "ask-1" in state["pending"]
    finally:

        async def close():
            await subscription.__aexit__(None, None, None)
            await store().redis.delete(key(sid), key(topic, "current"))

        client.portal.call(close)
