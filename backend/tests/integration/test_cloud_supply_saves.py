"""A cloud spec the provider cannot build is refused where it is saved.

The provider's offering is the range: on the day this was written dev's disk
stopped at 128 GB, and a room holding 256 GB failed only when a machine was
being made, with nothing saying which number was wrong. These go through the
real save routes and the real create path, with MicroCloud faked at its client.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.identity.actor import Actor
from app.domain.machine import enrollment
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.machine.services import MachineService
from tests.conftest import seed_user
from tests.integration.conftest import post_project
from tests.unit.test_machine_service import OFFERING, FakeMicroCloud

# What the provider will build: the disk ceiling is the one that bit on dev.
SUPPLY = {
    **OFFERING,
    "coresMax": 32,
    "memoryMbMax": 131072,
    "diskGbMin": 2,
    "diskGbMax": 128,
}


def _custom(disk_gb: int) -> dict:
    return {
        "name": "云端 · 自定义配置",
        "profile": "cloud",
        "device_id": None,
        "cores": 4,
        "memory_mb": 8192,
        "disk_gb": disk_gb,
    }


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(settings, "microcloud_base_url", "https://cloud.example")
    monkeypatch.setattr(settings, "microcloud_tenant_secret", "test-only")
    monkeypatch.setattr(settings, "microcloud_offering_id", 0)
    fake = FakeMicroCloud(offerings=SUPPLY)
    monkeypatch.setattr("app.domain.machine.supply.MicroCloudClient", lambda: fake)
    monkeypatch.setattr("app.domain.machine.services.MicroCloudClient", lambda: fake)

    async def _keypair():
        return "private", "ssh-ed25519 public"

    monkeypatch.setattr(enrollment, "generate_keypair", _keypair)
    return fake


@pytest.fixture
def owner(client):
    handle = f"supply-{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {seed_user(client, handle)}"}
    pid = post_project(client, json={"name": "供应范围"}, headers=headers).json()[
        "data"
    ]["id"]
    tid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=headers,
    ).json()["data"]["id"]
    return {"handle": handle, "headers": headers, "pid": pid, "tid": tid}


def _room_choice(client, owner) -> dict:
    return client.get(
        f"/topics/{owner['tid']}/compute-profile", headers=owner["headers"]
    ).json()["data"]["choice"]


def _project_default(client, owner) -> dict:
    return client.get(
        f"/projects/{owner['pid']}/compute-configs", headers=owner["headers"]
    ).json()["data"]["default"]


def _ensure(client, owner, cloud):
    async def _run():
        async with client.test_request_factory() as session:
            machine = await MachineService(session, cloud).ensure_topic_machine(
                uuid.UUID(owner["tid"]), actor=Actor(owner["handle"], 1, "token")
            )
            await session.commit()
            return machine

    return client.portal.call(_run)


def _machines(client, owner) -> list:
    async def _run():
        async with client.test_factory() as session:
            return await ProjectMachineRepository(session).list_for_project(
                uuid.UUID(owner["pid"])
            )

    return client.portal.call(_run)


def test_project_default_beyond_the_supply_is_refused_and_left_as_it_was(
    client, cloud, owner
):
    before = _project_default(client, owner)
    response = client.put(
        f"/projects/{owner['pid']}/compute-configs",
        json={"default": _custom(256)},
        headers=owner["headers"],
    )
    assert response.status_code == 422
    assert "磁盘" in response.json()["message"]
    assert "128" in response.json()["message"]
    assert _project_default(client, owner) == before


def test_room_choice_beyond_the_supply_is_refused_and_left_as_it_was(
    client, cloud, owner
):
    before = _room_choice(client, owner)
    response = client.put(
        f"/topics/{owner['tid']}/compute-profile",
        json={"choice": _custom(256)},
        headers=owner["headers"],
    )
    assert response.status_code == 422
    assert "磁盘" in response.json()["message"]
    assert _room_choice(client, owner) == before
    assert cloud.created == []


def test_a_spec_at_the_edge_is_saved_and_built_as_asked(
    client, cloud, owner, monkeypatch
):
    saved = client.put(
        f"/projects/{owner['pid']}/compute-configs",
        json={"default": _custom(128)},
        headers=owner["headers"],
    )
    assert saved.status_code == 200
    assert _project_default(client, owner)["disk_gb"] == 128

    room = client.put(
        f"/topics/{owner['tid']}/compute-profile",
        json={"choice": _custom(128)},
        headers=owner["headers"],
    )
    assert room.status_code == 200
    assert _room_choice(client, owner)["disk_gb"] == 128

    monkeypatch.setattr(
        MachineService, "require_use_authority", AsyncMock(return_value=None)
    )
    _ensure(client, owner, cloud)
    [body] = cloud.created
    assert (body["cores"], body["memoryMb"], body["diskGb"]) == (4, 8192, 128)


def test_an_unreadable_supply_lets_the_save_through_and_the_create_refuses(
    client, cloud, owner, monkeypatch
):
    cloud.list_offerings = AsyncMock(side_effect=MicroCloudError("unreachable"))
    saved = client.put(
        f"/topics/{owner['tid']}/compute-profile",
        json={"choice": _custom(256)},
        headers=owner["headers"],
    )
    assert saved.status_code == 200
    assert _room_choice(client, owner)["disk_gb"] == 256

    # The provider answers again by the time a machine is wanted.
    del cloud.list_offerings
    monkeypatch.setattr(
        MachineService, "require_use_authority", AsyncMock(return_value=None)
    )
    with pytest.raises(ValidationError, match="磁盘"):
        _ensure(client, owner, cloud)
    assert cloud.created == []
    assert _machines(client, owner) == []
