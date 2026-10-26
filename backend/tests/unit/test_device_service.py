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
from app.domain.device.supply import Supply
from tests.support.devices import enrollment_team


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


# These exercise approve mechanics (name / idempotency / token), where supply is
# incidental — so they enrol as the human connector door does, self-hosted.
async def test_start_then_poll_pending_then_approved():
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("andyl-macbook")

    assert (await service.poll(code))["status"] == DeviceStatus.PENDING

    device = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, owner),
    )
    poll = await service.poll(code)
    assert poll["status"] == DeviceStatus.APPROVED
    assert (
        await service.verify_token(poll["token"] or "")
    ).device_id == device.device_id
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
        team_id=await enrollment_team(service, uuid.uuid4()),
    )
    assert d1.name == "Andys-MacBook-Pro-510"
    # ...a name from the approval page overrides it.
    code2 = await service.start("Andys-MacBook-Pro-510")
    d2 = await service.approve(
        code2,
        owner_user_id=uuid.uuid4(),
        supply=Supply.self_hosted,
        name="  andy-studio  ",
        team_id=await enrollment_team(service, uuid.uuid4()),
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
            team_id=await enrollment_team(service, uuid.uuid4()),
        )
        assert device.name and device.name != "unnamed"


async def test_verify_token_identifies_device_only_when_valid():
    service, _ = _service()
    code = await service.start("m")
    device = await service.approve(
        code,
        owner_user_id=uuid.uuid4(),
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, uuid.uuid4()),
    )
    token = (await service.poll(code))["token"] or ""
    assert (await service.verify_token(token)).device_id == device.device_id
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
        team_id=await enrollment_team(service, owner),
    )
    d2 = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, owner),
    )
    assert d1.device_id == d2.device_id


async def test_the_token_is_handed_out_once():
    """The poll that brings the approval hands the machine its token; the same
    code polled again gets nothing, so a code seen later cannot be turned into
    a second copy of the machine's credential."""
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("m")
    await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, owner),
    )
    assert (await service.poll(code))["token"]
    with pytest.raises(NotFoundError):
        await service.poll(code)


async def test_only_a_machine_the_platform_provisioned_gets_a_new_token():
    """Replacing a token cuts off whatever holds the old one: the platform does
    it for its own machines as it bootstraps them, never for a person's."""
    service, _ = _service()
    owner = uuid.uuid4()
    code = await service.start("mine")
    mine = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, owner),
    )
    held = (await service.poll(code))["token"] or ""
    with pytest.raises(ForbiddenError):
        await service.bootstrap_token(mine.device_id)
    assert (await service.verify_token(held)).device_id == mine.device_id

    code = await service.start("cloud")
    cloud = await service.approve(
        code,
        owner_user_id=owner,
        supply=Supply.cloud,
        team_id=await enrollment_team(service, owner),
    )
    first = await service.bootstrap_token(cloud.device_id)
    second = await service.bootstrap_token(cloud.device_id)
    assert await service.verify_token(first) is None
    assert (await service.verify_token(second)).device_id == cloud.device_id


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
            team_id=await enrollment_team(service, uuid.uuid4()),
        )


async def test_delete_owned_forgets_the_device():
    service = DeviceService(InMemoryDeviceRepository())
    owner = uuid.uuid4()
    device = await service.approve(
        await service.start("laptop"),
        owner_user_id=owner,
        supply=Supply.self_hosted,
        team_id=await enrollment_team(service, owner),
    )

    await service.remove(device.device_id, actor_user_id=owner)

    assert await service.get_device(device.device_id) is None


async def test_get_devices_answers_the_ids_that_exist_and_omits_the_rest():
    service = DeviceService(InMemoryDeviceRepository())
    owner = uuid.uuid4()
    enrolled = [
        await service.approve(
            await service.start(name),
            owner_user_id=owner,
            supply=Supply.self_hosted,
            team_id=await enrollment_team(service, owner),
        )
        for name in ("one", "two")
    ]

    found = await service.get_devices(
        [enrolled[0].device_id, "never-enrolled", enrolled[1].device_id]
    )

    assert {device_id: device.name for device_id, device in found.items()} == {
        enrolled[0].device_id: "one",
        enrolled[1].device_id: "two",
    }
    assert await service.get_devices([]) == {}
