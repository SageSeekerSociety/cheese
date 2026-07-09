"""Device flow: start / approve / poll / token / project binding + ownership."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService, DeviceStatus


def _service(
    now: datetime | None = None,
) -> tuple[DeviceService, dict[str, datetime]]:
    clock = {"t": now or datetime(2026, 7, 9, tzinfo=UTC)}
    service = DeviceService(
        InMemoryDeviceRepository(),
        code_ttl=timedelta(minutes=10),
        now=lambda: clock["t"],
    )
    return service, clock


async def test_start_then_poll_pending_then_approved():
    service, _ = _service()
    owner, agent = uuid.uuid4(), uuid.uuid4()
    code = await service.start("andyl-macbook")

    assert (await service.poll(code))["status"] == DeviceStatus.PENDING

    device = await service.approve(code, owner_user_id=owner, agent_user_id=agent)
    poll = await service.poll(code)
    assert poll["status"] == DeviceStatus.APPROVED
    assert poll["token"] == device.token
    assert poll["device_id"] == device.device_id
    assert device.owner_user_id == owner
    assert device.agent_user_id == agent


async def test_verify_token_identifies_device_only_when_valid():
    service, _ = _service()
    code = await service.start("m")
    device = await service.approve(
        code, owner_user_id=uuid.uuid4(), agent_user_id=uuid.uuid4()
    )
    assert (await service.verify_token(device.token)).device_id == device.device_id
    assert await service.verify_token("nope") is None
    assert await service.verify_token("") is None


async def test_approve_is_idempotent_same_token():
    service, _ = _service()
    owner, agent = uuid.uuid4(), uuid.uuid4()
    code = await service.start("m")
    d1 = await service.approve(code, owner_user_id=owner, agent_user_id=agent)
    d2 = await service.approve(code, owner_user_id=owner, agent_user_id=agent)
    assert d1.device_id == d2.device_id
    assert d1.token == d2.token  # never mints a second credential


async def test_expired_code_is_rejected():
    service, clock = _service()
    code = await service.start("m")
    clock["t"] = clock["t"] + timedelta(minutes=11)
    with pytest.raises(NotFoundError):
        await service.poll(code)
    with pytest.raises(NotFoundError):
        await service.approve(
            code, owner_user_id=uuid.uuid4(), agent_user_id=uuid.uuid4()
        )


async def test_project_binding_and_ownership_guard():
    service, _ = _service()
    owner, other = uuid.uuid4(), uuid.uuid4()
    project = uuid.uuid4()
    code = await service.start("m")
    device = await service.approve(
        code, owner_user_id=owner, agent_user_id=uuid.uuid4()
    )

    assert not await service.serves_project(device.device_id, project)
    await service.assign_to_project(device.device_id, project, actor_user_id=owner)
    assert await service.serves_project(device.device_id, project)
    served = await service.list_devices_for_project(project)
    assert [d.device_id for d in served] == [device.device_id]

    # A non-owner cannot manage the device's project assignments.
    with pytest.raises(ForbiddenError):
        await service.assign_to_project(
            device.device_id, uuid.uuid4(), actor_user_id=other
        )

    await service.unassign_from_project(device.device_id, project, actor_user_id=owner)
    assert not await service.serves_project(device.device_id, project)
