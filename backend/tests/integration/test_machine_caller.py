"""Who a sandbox's request belongs to, as the backend's /machine routes read it.

The rules, as a person would state them:

- the caller is the session whose current lease is the state the request came
  in on, on that device: nothing in the request names it otherwise;
- only the process that owns the device links can say so, and it proves it
  with the device connection secret; without the secret nobody is anyone;
- a session whose lease ended, or that holds another state now, is nobody from
  its next request on.
"""

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.agent_session.models import AgentSession
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _owner_secret(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")


def _whoami(client, *, device, state, secret="owner-secret"):
    return client.get(
        "/machine/whoami",
        headers={
            "x-device-connection-secret": secret,
            "x-machine-device": device,
            "x-machine-state": state,
        },
    )


async def test_the_session_holding_the_state_is_the_caller(client):
    room = await _room(client)
    answer = _whoami(client, device=room.old_device, state="/old/state")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "session": str(room.session_id),
        "project": str(room.project_id),
        "conversation": str(room.conversation_id),
        "agent": room.agent_handle,
    }


async def test_without_the_owners_secret_nobody_is_anyone(client):
    room = await _room(client)
    for secret in ("", "guessed"):
        answer = _whoami(
            client, device=room.old_device, state="/old/state", secret=secret
        )
        assert answer.status_code == 401, answer.text


async def test_the_same_state_on_another_device_is_nobody(client):
    room = await _room(client)
    assert (
        _whoami(client, device=room.new_device, state="/old/state").status_code == 403
    )


async def test_a_session_whose_lease_moved_on_is_nobody_from_its_next_request(client):
    room = await _room(client)
    assert (
        _whoami(client, device=room.old_device, state="/old/state").status_code == 200
    )
    async with client.test_factory() as db:
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == room.session_id)
            .values(work_lease={**room.lease, "state": "/new/state"})
        )
        await db.commit()
    assert (
        _whoami(client, device=room.old_device, state="/old/state").status_code == 403
    )
    async with client.test_factory() as db:
        await db.execute(
            update(AgentSession)
            .where(AgentSession.id == room.session_id)
            .values(work_lease={**room.lease, "status": "released"})
        )
        await db.commit()
    assert (
        _whoami(client, device=room.old_device, state="/old/state").status_code == 403
    )


async def test_a_request_on_the_socket_comes_back_with_whose_it_is(client, monkeypatch):
    """The whole path behind the connector: frames on the device link, the
    relay naming the device and state, the backend answering from its lease."""
    import asyncio
    import base64
    import json

    import httpx

    from app.api import machine_relay
    from app.domain.agent.device_hub import DeviceHub
    from app.domain.agent.device_hub import machine as machine_streams

    room = await _room(client)
    hub, sent = DeviceHub(), []

    class Transport:
        async def send_json(self, msg):
            sent.append(msg)

    await hub.attach_device(room.old_device, Transport())

    # The app runs on the test client's own loop; reach it through the client,
    # as the connection owner reaches the backend over HTTP.
    def through_the_client(request: httpx.Request) -> httpx.Response:
        answer = client.request(
            request.method,
            request.url.path,
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            answer.status_code,
            headers={"content-type": answer.headers["content-type"]},
            stream=httpx.ByteStream(answer.content),
        )

    machine_streams.serve(
        machine_relay.forwarder(
            httpx.AsyncClient(transport=httpx.MockTransport(through_the_client)),
            "http://backend",
        )
    )
    try:
        await hub.on_device_message(
            room.old_device, {"t": "machine.open", "id": "s", "path": "/old/state"}
        )
        request = b"GET /whoami HTTP/1.1\r\nhost: cheese\r\n\r\n"
        await hub.on_device_message(
            room.old_device,
            {
                "t": "machine.data",
                "id": "s",
                "data": base64.b64encode(request).decode(),
            },
        )
        for _ in range(500):
            if any(m["t"] == "machine.close" and m.get("id") == "s" for m in sent):
                break
            await asyncio.sleep(0.01)
    finally:
        machine_streams.serve(None)
    answer = b"".join(
        base64.b64decode(m["data"]) for m in sent if m["t"] == "machine.data"
    )
    head, _, body = answer.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200"), answer
    assert json.loads(body)["session"] == str(room.session_id)


async def test_a_secret_header_with_any_bytes_is_refused_not_a_crash(client):
    room = await _room(client)
    answer = client.get(
        "/machine/whoami",
        headers=[
            ("x-device-connection-secret", "sécret".encode("latin-1")),
            ("x-machine-device", room.old_device),
            ("x-machine-state", "/old/state"),
        ],
    )
    assert answer.status_code == 401, answer.text
