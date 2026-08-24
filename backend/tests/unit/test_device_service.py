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
from app.domain.device.supply import Supply, Visibility


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


# These exercise approve mechanics (name / idempotency / token), where the device's
# visibility is incidental — so they enrol with the honest self-hosted default
# (isolated), the value the human connector door now records unless the approver
# opts into whole-machine.
async def test_start_then_poll_pending_then_approved():
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("andyl-macbook")

    assert (await service.poll(code))["status"] == DeviceStatus.PENDING

    device = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    poll = await service.poll(code)
    assert poll["status"] == DeviceStatus.APPROVED
    assert poll["token"] == device.token
    assert poll["device_id"] == device.device_id
    assert device.owner_user_id == owner
    # A device is pure compute — it carries no agent identity.
    assert not hasattr(device, "agent_user_id")


async def test_approve_name_override_else_keeps_cli_name():
    service, _ = _service()
    # Blank/omitted name keeps the name the cli proposed at start (its hostname)...
    code1 = await service.start("Andys-MacBook-Pro-510")
    d1 = await service.approve(
        code1,
        owner_user_id=uuid.uuid4(),
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    assert d1.name == "Andys-MacBook-Pro-510"
    # ...a name from the approval page overrides it.
    code2 = await service.start("Andys-MacBook-Pro-510")
    d2 = await service.approve(
        code2,
        owner_user_id=uuid.uuid4(),
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
        name="  andy-studio  ",
    )
    assert d2.name == "andy-studio"


async def test_start_generates_a_real_name_never_unnamed():
    service, _ = _service()
    # The cli may send no name; the fallback must be a real generated label, never the
    # literal "unnamed" (which is what the user saw before).
    for empty in (None, "", "   "):
        code = await service.start(empty)
        device = await service.approve(
            code,
            owner_user_id=uuid.uuid4(),
            supply=Supply.self_hosted,
            visibility=Visibility.isolated,
        )
        assert device.name and device.name != "unnamed"


async def test_verify_token_identifies_device_only_when_valid():
    service, _ = _service()
    code = await service.start("m")
    device = await service.approve(
        code,
        owner_user_id=uuid.uuid4(),
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    assert (await service.verify_token(device.token)).device_id == device.device_id
    assert await service.verify_token("nope") is None
    assert await service.verify_token("") is None


async def test_approve_is_idempotent_same_token():
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("m")
    d1 = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    d2 = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
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
            code,
            owner_user_id=uuid.uuid4(),
            supply=Supply.self_hosted,
            visibility=Visibility.isolated,
        )


async def test_project_binding_and_ownership_guard():
    service, _ = _service()
    owner, other = uuid.uuid4(), uuid.uuid4()
    project = uuid.uuid4()
    code = await service.start("m")
    device = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
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


async def test_human_management_never_lists_or_mutates_a_cloud_endpoint():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api.routes.connector import BindTeamRequest, register_device_for_team

    service, _ = _service()
    owner, team_id = uuid.uuid4(), 7

    async def _enroll(supply: Supply):
        return await service.approve(
            await service.start(supply.value),
            owner_user_id=owner,
            supply=supply,
            visibility=Visibility.isolated,
        )

    hosted = await _enroll(Supply.self_hosted)
    cloud = await _enroll(Supply.cloud)
    for device in (hosted, cloud):
        await service.assign_to_team(device.device_id, team_id, actor_user_id=owner)

    assert [d.device_id for d in await service.list_owned(owner)] == [hosted.device_id]
    assert [d.device_id for d in await service.list_devices_for_team(team_id)] == [
        hosted.device_id
    ]
    with pytest.raises(NotFoundError):
        await service.rename_owned(cloud.device_id, "no", actor_user_id=owner)
    with pytest.raises(NotFoundError):
        await service.delete_owned(cloud.device_id, actor_user_id=owner)
    with pytest.raises(NotFoundError):
        await service.unassign_from_team(cloud.device_id, team_id, actor_user_id=owner)

    resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(authenticated=True, user_id=owner)
        )
    )
    with pytest.raises(NotFoundError):
        await register_device_for_team(
            cloud.device_id,
            BindTeamRequest(team_id=team_id),
            resolver,
            service,
            MagicMock(),
        )


# --- #420: deleting a device revokes its ccproxy ticket first ---------------


async def test_delete_owned_revokes_ccproxy_ticket_before_forgetting():
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    from app.domain.device.ccproxy_tenant import CcproxyTenantClient

    ccproxy = SimpleNamespace(delete_machine=AsyncMock(return_value=None))
    repo = InMemoryDeviceRepository()
    service = DeviceService(repo, ccproxy=cast(CcproxyTenantClient, ccproxy))
    owner = uuid.uuid4()
    device = await service.approve(
        await service.start("dev-box"),
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    device.ccproxy_machine_id = 161  # registered at ccproxy (part 2 writes this)

    await service.delete_owned(device.device_id, actor_user_id=owner)

    ccproxy.delete_machine.assert_awaited_once_with(161)
    assert await service.get_device(device.device_id) is None


async def test_delete_owned_keeps_the_device_when_revocation_is_unconfirmed():
    """A deleted row with a live ticket is the hazard #420 closes — so an
    unconfirmed revocation must fail the deletion, not the other way round."""
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    from app.domain.device.ccproxy_tenant import (
        CcproxyTenantClient,
        CcproxyTenantError,
    )

    ccproxy = SimpleNamespace(
        delete_machine=AsyncMock(
            side_effect=CcproxyTenantError("engine unreachable", status=502)
        )
    )
    repo = InMemoryDeviceRepository()
    service = DeviceService(repo, ccproxy=cast(CcproxyTenantClient, ccproxy))
    owner = uuid.uuid4()
    device = await service.approve(
        await service.start("dev-box"),
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    device.ccproxy_machine_id = 161

    with pytest.raises(CcproxyTenantError):
        await service.delete_owned(device.device_id, actor_user_id=owner)

    assert await service.get_device(device.device_id) is not None


async def test_delete_owned_of_a_plain_device_never_dials_ccproxy():
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock

    from app.domain.device.ccproxy_tenant import CcproxyTenantClient

    ccproxy = SimpleNamespace(delete_machine=AsyncMock())
    service = DeviceService(
        InMemoryDeviceRepository(), ccproxy=cast(CcproxyTenantClient, ccproxy)
    )
    owner = uuid.uuid4()
    device = await service.approve(
        await service.start("laptop"),
        owner_user_id=owner,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )

    await service.delete_owned(device.device_id, actor_user_id=owner)

    ccproxy.delete_machine.assert_not_awaited()
