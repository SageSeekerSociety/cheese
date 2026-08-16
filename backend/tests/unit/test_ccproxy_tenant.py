"""The ccproxy tenant client (#420: one revocable ticket per device).

Functional: a mock ccproxy records what the client sends; the tests assert the
requests (auth header, connector-mode body) and the client's reading of the
replies — especially DELETE, whose contract is confirmed revocation.
"""

import json

import httpx
import pytest

from app.domain.device.ccproxy_tenant import (
    CcproxyTenantClient,
    CcproxyTenantError,
)

pytestmark = pytest.mark.anyio


def _client(handler) -> CcproxyTenantClient:
    return CcproxyTenantClient(
        base_url="https://ccproxy.test",
        secret="tenant-secret",
        transport=httpx.MockTransport(handler),
    )


def _machine_payload(**overrides) -> dict:
    payload = {
        "id": 161,
        "tenantId": 7,
        "status": "created",
        "caCertInstalled": False,
        "hasCredential": False,
        "connector": True,
        "online": False,
    }
    payload.update(overrides)
    return payload


async def test_create_machine_is_connector_mode_and_bearer_authed():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json=_machine_payload(
                deviceToken="dt-abc",
                installCommand="curl -sSL https://ccproxy.test/install | sh -s dt-abc",
            ),
        )

    machine = await _client(handler).create_machine(label="dev-box")

    assert seen["auth"] == "Bearer tenant-secret"
    assert (seen["method"], seen["path"]) == ("POST", "/machine")
    # Connector-mode: no host — ccproxy must never be asked to SSH into a
    # device cheese manages.
    assert seen["body"] == {"label": "dev-box"}
    assert machine.machine_id == 161
    assert machine.device_token == "dt-abc"
    assert machine.install_command is not None and "dt-abc" in machine.install_command
    assert machine.has_credential is False


async def test_get_machine_reads_login_progress():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/machine/161"
        return httpx.Response(
            200, json=_machine_payload(status="ready", online=True, hasCredential=True)
        )

    machine = await _client(handler).get_machine(161)
    assert machine.status == "ready"
    assert machine.online is True
    assert machine.has_credential is True
    # get/list never carry the enrollment secrets.
    assert machine.device_token is None
    assert machine.install_command is None


async def test_delete_machine_accepts_confirmed_204():
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("DELETE", "/machine/161")
        return httpx.Response(204)

    await _client(handler).delete_machine(161)


async def test_delete_machine_treats_404_as_already_revoked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "no such machine"})

    await _client(handler).delete_machine(161)


async def test_delete_machine_raises_when_revocation_is_not_confirmed():
    """Anything but 204/404 means the ticket may still spend — the caller must
    NOT proceed to forget the device."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="engine unreachable")

    with pytest.raises(CcproxyTenantError) as excinfo:
        await _client(handler).delete_machine(161)
    assert excinfo.value.status == 502


async def test_unconfigured_client_refuses_rather_than_dials_nowhere():
    client = CcproxyTenantClient(base_url="", secret="")
    assert client.configured is False
    with pytest.raises(CcproxyTenantError):
        await client.create_machine(label="x")


async def test_error_carries_status_for_misconfiguration_triage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad tenant secret"})

    with pytest.raises(CcproxyTenantError) as excinfo:
        await _client(handler).get_machine(1)
    assert excinfo.value.status == 401
