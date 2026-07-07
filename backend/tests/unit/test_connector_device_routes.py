"""Contract tests for the device-flow HTTP endpoints (Act 2 step 3, HTTP half).

DB-free: a mini app mounting only the device-flow router over an in-memory
``DeviceService``, plus the shared ``BaseError`` handler. Pins the frozen wire shape
``cheese auth login`` depends on.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.connector_device import build_device_flow_router
from app.core.errors import BaseError, base_error_handler
from app.domain.device import Device, DeviceService, InMemoryDeviceRepository


@pytest.fixture
def env() -> tuple[TestClient, DeviceService]:
    service = DeviceService(InMemoryDeviceRepository())
    app = FastAPI()
    app.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]
    app.include_router(build_device_flow_router(service))
    return TestClient(app), service


async def _approve(service: DeviceService, code: str) -> Device:
    return await service.approve(code, actor_user_id=1)


def test_start_returns_code_and_frontend_approve_url(env: tuple[TestClient, DeviceService]) -> None:
    client, _ = env
    resp = client.post("/auth/device/start", json={"device_name": "laptop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_code"]
    assert body["interval"] == 1
    assert "/connect?code=" in body["approve_url"]
    assert body["approve_url"].endswith(body["device_code"])


def test_poll_is_pending_until_approved(env: tuple[TestClient, DeviceService]) -> None:
    client, service = env
    code = client.post("/auth/device/start", json={}).json()["device_code"]
    assert client.post("/auth/device/poll", json={"device_code": code}).json() == {
        "status": "pending"
    }

    with client:
        device = client.portal.call(_approve, service, code)
        approved = client.post("/auth/device/poll", json={"device_code": code}).json()
    assert approved["status"] == "approved"
    assert approved["token"] == device.token
    assert approved["device_id"] == device.device_id


def test_poll_unknown_code_is_404(env: tuple[TestClient, DeviceService]) -> None:
    client, _ = env
    assert client.post("/auth/device/poll", json={"device_code": "nope"}).status_code == 404


def test_rename_requires_bearer_and_updates(env: tuple[TestClient, DeviceService]) -> None:
    client, service = env
    # No token → 401.
    assert client.post("/auth/device/rename", json={"device_name": "x"}).status_code == 401

    code = client.post("/auth/device/start", json={"device_name": "old"}).json()["device_code"]
    with client:
        device = client.portal.call(_approve, service, code)
        ok = client.post(
            "/auth/device/rename",
            json={"device_name": "new-name"},
            headers={"Authorization": f"Bearer {device.token}"},
        )
        assert ok.status_code == 200
        assert ok.json() == {"ok": True, "device_name": "new-name"}

        bad = client.post(
            "/auth/device/rename",
            json={"device_name": "y"},
            headers={"Authorization": "Bearer garbage"},
        )
        assert bad.status_code == 404


def test_approve_binds_the_device_to_the_logged_in_owner(
    env: tuple[TestClient, DeviceService],
) -> None:
    from app.common.auth import get_current_user_id

    client, service = env
    client.app.dependency_overrides[get_current_user_id] = lambda: 4242  # type: ignore[attr-defined]
    try:
        code = client.post("/auth/device/start", json={"device_name": "m"}).json()["device_code"]
        resp = client.post("/auth/device/approve", json={"code": code})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        with client:
            device = client.portal.call(service.get_device, resp.json()["device_id"])
        assert device is not None
        assert device.owner_user_id == 4242  # bound to the approving user, not a project/agent
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_approve_unknown_code_is_404(env: tuple[TestClient, DeviceService]) -> None:
    from app.common.auth import get_current_user_id

    client, _ = env
    client.app.dependency_overrides[get_current_user_id] = lambda: 1  # type: ignore[attr-defined]
    try:
        assert client.post("/auth/device/approve", json={"code": "nope"}).status_code == 404
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
