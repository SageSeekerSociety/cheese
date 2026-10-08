"""A room that stops being read is ended, not buffered without bound.

The frames a subscriber has not read are held by the process, not by the
socket, and uvicorn's ping/pong cannot see the backlog: a consumer that keeps
answering pings while falling further behind looks healthy — a live socket with
a red dot — right up to the moment the process runs out of memory. So the
broker budgets each subscriber's queue in bytes (`MAX_SUBSCRIBER_BYTES`) and,
past that, stops feeding it and hands its relay a sentinel to end that room
on (`closed`, code 1013). The other rooms on the same connection are not behind
and keep their feed.

It is the room ending and not an `error` frame the client would have to learn: a
drop is already something the client recovers from — it resubscribes, refetches
history and resumes — so a slow reader pays with a resubscribe instead of the
process paying with memory.
"""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.domain.agent.realtime.broker import get_broker
from tests.integration.conftest import (
    post_project,
    room_socket,
    session_auth_headers,
)


def _room(client) -> str:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    return client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def test_a_socket_that_stops_reading_is_closed_rather_than_buffered(
    client, monkeypatch
):
    tid = _room(client)
    broker = get_broker()
    monkeypatch.setattr(broker, "_max_subscriber_bytes", 200)
    over_budget = {"type": "delta", "text": "x" * 150}

    with room_socket(client, tid, "alice") as ws:

        async def burst() -> None:
            # Nothing awaits a suspension point between these two publishes, so
            # the relay never gets a chance to drain — which is what a client
            # that stopped reading looks like from the publisher's side.
            for _ in range(2):
                await broker.publish(str(tid), over_budget)

        ws.portal.call(burst)
        # What the queue had already taken, and then the server hangs up.
        assert ws.receive_json() == over_budget
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1013
