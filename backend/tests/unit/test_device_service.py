"""Device flow: start / approve / poll / token / project binding + ownership.

A device is pure compute (execution-architecture v3) — ``approve`` binds it to its
owner and mints a token, but grants NO agent identity. The agent a screen runs as is
resolved per project/topic at turn time, not stored on the device.
"""

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
    owner = uuid.uuid4()
    code = await service.start("andyl-macbook")

    assert (await service.poll(code))["status"] == DeviceStatus.PENDING

    device = await service.approve(code, owner_user_id=owner)
    poll = await service.poll(code)
    assert poll["status"] == DeviceStatus.APPROVED
    assert poll["token"] == device.token
    assert poll["device_id"] == device.device_id
    assert device.owner_user_id == owner
    # A device is pure compute — it carries no agent identity.
    assert not hasattr(device, "agent_user_id")


async def test_approve_name_override_else_keeps_cli_name():
    service, _ = _service()
    # Blank/omitted name keeps the name the cli proposed at start...
    code1 = await service.start("cli-proposed")
    d1 = await service.approve(code1, owner_user_id=uuid.uuid4())
    assert d1.name == "cli-proposed"
    # ...a name from the approval page overrides it (fixes an "unnamed" node).
    code2 = await service.start("unnamed")
    d2 = await service.approve(
        code2, owner_user_id=uuid.uuid4(), name="  andy-macbook  "
    )
    assert d2.name == "andy-macbook"


async def test_verify_token_identifies_device_only_when_valid():
    service, _ = _service()
    code = await service.start("m")
    device = await service.approve(code, owner_user_id=uuid.uuid4())
    assert (await service.verify_token(device.token)).device_id == device.device_id
    assert await service.verify_token("nope") is None
    assert await service.verify_token("") is None


async def test_approve_is_idempotent_same_token():
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("m")
    d1 = await service.approve(code, owner_user_id=owner)
    d2 = await service.approve(code, owner_user_id=owner)
    assert d1.device_id == d2.device_id
    assert d1.token == d2.token  # never mints a second credential


async def test_expired_code_is_rejected():
    service, clock = _service()
    code = await service.start("m")
    clock["t"] = clock["t"] + timedelta(minutes=11)
    with pytest.raises(NotFoundError):
        await service.poll(code)
    with pytest.raises(NotFoundError):
        await service.approve(code, owner_user_id=uuid.uuid4())


async def test_project_binding_and_ownership_guard():
    service, _ = _service()
    owner, other = uuid.uuid4(), uuid.uuid4()
    project = uuid.uuid4()
    code = await service.start("m")
    device = await service.approve(code, owner_user_id=owner)

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
