"""The platform's API, asked by a sandbox on its platform socket.

The rules, as a person would state them:

- the sandbox holds no token; its request is served by the route it names as
  the session leasing it now, under that session's own credential (its lease
  and resource included), made for the request;
- whatever the sandbox sent that would say who is calling is not what it is
  served as;
- the model routes and the socket's own routes are not reached this way;
- what cannot go down the socket is handed the session's credential for a
  minute, asked for right before it is used;
- nobody but the device link owner is anyone here.
"""

import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import held_by_a_machine, scoped_token_claims
from app.domain.machine.caller import caller_of, credential_of, machine_proxy_proof
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")


def _ask(client, path, *, device, secret="owner-secret", headers=None):
    return client.get(
        f"/machine/api/{path}",
        headers={
            "x-device-connection-secret": secret,
            "x-machine-device": device,
            "x-machine-state": "/old/state",
            **(headers or {}),
        },
    )


async def test_the_sandbox_is_answered_as_its_session(client):
    room = await _room(client)
    answer = _ask(client, f"topics/{room.topic_id}", device=room.old_device)
    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["id"] == str(room.topic_id)


async def test_a_token_the_sandbox_sends_is_not_who_it_is_served_as(client):
    room = await _room(client)
    other = await _room(client)
    answer = _ask(
        client,
        f"topics/{room.topic_id}",
        device=room.old_device,
        headers=other.agent,
    )
    assert answer.status_code == 200, answer.text
    # Served as the session leasing this sandbox, not the token it sent.
    elsewhere = _ask(
        client,
        f"topics/{other.topic_id}",
        device=room.old_device,
        headers=other.agent,
    )
    assert elsewhere.status_code in (403, 404), elsewhere.text


async def test_the_credential_names_the_sessions_lease_and_calls_no_model(client):
    room = await _room(client)
    async with client.test_factory() as db:
        caller = await caller_of(db, room.old_device, "/old/state")
        assert caller is not None
        credential = await credential_of(db, caller)
    claims = scoped_token_claims(credential or "")
    assert claims is not None
    assert claims["session"] == str(room.session_id)
    assert claims["lease"] == room.lease["generation"]
    assert claims["r"] == room.lease["room_resource_id"]
    assert held_by_a_machine(claims)


async def test_the_model_routes_and_the_sockets_own_are_not_reached_here(client):
    room = await _room(client)
    for path in ("llm/v1/models", "machine/whoami"):
        assert _ask(client, path, device=room.old_device).status_code == 404


async def test_without_the_owner_or_a_lease_nobody_is_served(client):
    room = await _room(client)
    path = f"topics/{room.topic_id}"
    assert (
        _ask(client, path, device=room.old_device, secret="guessed").status_code == 401
    )
    assert _ask(client, path, device=room.new_device).status_code == 403
    assert _ask(client, path, device=str(uuid.uuid4())).status_code == 403


async def test_a_credential_handed_out_lasts_a_minute_and_names_the_session(client):
    import time

    room = await _room(client)
    # /machine/credential is gone; inside the sandbox, /machine/proxy-credential
    # without the host-side proof is refused with 403.
    assert (
        client.get(
            "/machine/credential",
            headers={
                "x-device-connection-secret": "owner-secret",
                "x-machine-device": room.old_device,
                "x-machine-state": "/old/state",
            },
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/machine/proxy-credential",
            headers={
                "x-device-connection-secret": "owner-secret",
                "x-machine-device": room.old_device,
                "x-machine-state": "/old/state",
            },
        ).status_code
        == 403
    )
    answer = client.get(
        "/machine/proxy-credential",
        headers={
            "x-device-connection-secret": "owner-secret",
            "x-machine-device": room.old_device,
            "x-machine-state": "/old/state",
            "x-cheese-proxy-proof": machine_proxy_proof(str(room.project_id)),
        },
    )
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    claims = scoped_token_claims(answer.json()["token"])
    assert claims is not None
    assert claims["session"] == str(room.session_id)
    assert held_by_a_machine(claims)
    assert claims["exp"] - time.time() <= 61
    refused = client.get(
        "/machine/proxy-credential",
        headers={"x-machine-device": room.old_device, "x-machine-state": "/old/state"},
    )
    assert refused.status_code == 401
    # A machine-held credential is refused on ordinary platform API routes,
    # session-host routes and execution routes; the sandbox must ask
    # /machine/api/... on its socket.
    machine_headers = {"Authorization": f"Bearer {answer.json()['token']}"}
    assert (
        client.get(f"/topics/{room.topic_id}", headers=machine_headers).status_code
        == 401
    )
    assert (
        client.post(
            f"/topics/{room.topic_id}/execution/{room.lease['room_resource_id']}",
            headers=machine_headers,
            json={"method": "ping", "params": {}},
        ).status_code
        == 401
    )
    assert client.get("/sandbox/cli/cheese", headers=machine_headers).status_code == 401


async def test_sandbox_forge_token_on_socket_returns_placeholder_to_sandbox_and_real_to_proxy(
    client, monkeypatch
):
    from types import SimpleNamespace

    room = await _room(client)
    binding = SimpleNamespace(
        kind="github_app",
        repo="acme/widgets",
        url="https://github.com/acme/widgets.git",
        api_url="https://api.github.com",
        default_branch="main",
    )

    async def binding_for_project(_id, _session):
        return binding

    class Minter:
        async def installation_token(self):
            return "ghs_real_single_repo_token", "2026-10-27T10:00:00Z"

        async def granted_permissions(self):
            return {"contents": "write"}

    async def tokens_for_project(_id, _session):
        return Minter()

    monkeypatch.setattr(
        "app.domain.project.forge.binding_for_project", binding_for_project
    )
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project", tokens_for_project
    )

    from_sandbox = _ask(client, "sandbox/forge-token", device=room.old_device)
    assert from_sandbox.status_code == 200, from_sandbox.text
    assert from_sandbox.json()["data"]["token"] == "placeholder"

    from_proxy = _ask(
        client,
        "sandbox/forge-token",
        device=room.old_device,
        headers={"x-cheese-proxy-proof": machine_proxy_proof(str(room.project_id))},
    )
    assert from_proxy.status_code == 200, from_proxy.text
    assert from_proxy.json()["data"]["token"] == "ghs_real_single_repo_token"
