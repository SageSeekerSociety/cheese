"""Contract tests for the owner-facing device→project admin routes.

DB-free mini app over an in-memory ``DeviceService`` with a faked membership check
and an overridden auth dependency. Pins the two-gate rule: the caller must own the
device (service) AND be a member of the target project (route).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.connector_device import build_device_admin_router
from app.common.auth import get_current_user_id
from app.core.errors import BaseError, base_error_handler
from app.domain.device import Device, DeviceService, InMemoryDeviceRepository

OWNER = 42


@pytest.fixture
def env() -> tuple[TestClient, DeviceService, set[tuple[int, int]]]:
    service = DeviceService(InMemoryDeviceRepository())
    members: set[tuple[int, int]] = set()

    async def is_member(project_id: int, user_id: int) -> bool:
        return (project_id, user_id) in members

    app = FastAPI()
    app.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]
    app.include_router(build_device_admin_router(service, is_member))
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    return TestClient(app), service, members


async def _enroll(service: DeviceService, owner: int) -> Device:
    return await service.approve(await service.start("m"), actor_user_id=owner)


def test_owner_and_member_can_assign_then_list(
    env: tuple[TestClient, DeviceService, set[tuple[int, int]]],
) -> None:
    client, service, members = env
    with client:
        device = client.portal.call(_enroll, service, OWNER)
        members.add((100, OWNER))  # OWNER is a member of project 100

        assign = client.post(f"/connector/devices/{device.device_id}/projects", json={"project_id": 100})
        assert assign.status_code == 200

        listed = client.get(f"/connector/devices/{device.device_id}/projects")
        assert listed.status_code == 200
        assert listed.json()["project_ids"] == [100]

        unassign = client.delete(f"/connector/devices/{device.device_id}/projects/100")
        assert unassign.status_code == 200
        assert client.get(f"/connector/devices/{device.device_id}/projects").json()["project_ids"] == []


def test_non_member_cannot_assign(
    env: tuple[TestClient, DeviceService, set[tuple[int, int]]],
) -> None:
    client, service, _members = env
    with client:
        device = client.portal.call(_enroll, service, OWNER)
        # OWNER owns the device but is not a member of project 100 → 403.
        resp = client.post(f"/connector/devices/{device.device_id}/projects", json={"project_id": 100})
        assert resp.status_code == 403


def test_non_owner_cannot_assign(
    env: tuple[TestClient, DeviceService, set[tuple[int, int]]],
) -> None:
    client, service, members = env
    with client:
        device = client.portal.call(_enroll, service, 999)  # owned by someone else
        members.add((100, OWNER))  # OWNER is a project member but not the device owner
        resp = client.post(f"/connector/devices/{device.device_id}/projects", json={"project_id": 100})
        assert resp.status_code == 403
