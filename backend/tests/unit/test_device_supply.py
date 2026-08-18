"""供给形式 (#282 决定 2): a device records HOW it came to be ours, and that fact
decides what the platform may do to it.

The behaviour under test is the one #282 exists to protect: cloud supply means the
platform opened the machine and may reclaim it; self-hosted means a human enrolled
a machine they already keep running, and the platform may only stop USING it. The
same physical machine is either, depending on which enrolment door it came through
— 入口决定待遇，不是硬件决定待遇.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ForbiddenError
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply, Visibility


def _service() -> DeviceService:
    return DeviceService(
        InMemoryDeviceRepository(),
        code_ttl=timedelta(minutes=10),
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )


async def _enrol(service: DeviceService, supply: Supply, owner: int) -> str:
    # These tests are about SUPPLY (the destroy axis); visibility is incidental, so
    # they enrol with the conservative default (isolated) — the value both axes now
    # take when an entry point does not opt into the dangerous one.
    code = await service.start("a-machine")
    device = await service.approve(
        code, owner_user_id=owner, supply=supply, visibility=Visibility.isolated
    )
    return device.device_id


async def test_each_enrolment_door_records_its_own_supply():
    service = _service()
    owner = uuid.uuid4()

    self_hosted = await service.get_device(
        await _enrol(service, Supply.self_hosted, owner)
    )
    cloud = await service.get_device(await _enrol(service, Supply.cloud, owner))

    assert self_hosted is not None and cloud is not None
    assert self_hosted.supply is Supply.self_hosted
    assert cloud.supply is Supply.cloud
    # Nothing about the machine itself distinguishes them — same name, same owner,
    # same everything. Only the door they came through.
    assert self_hosted.name == cloud.name


async def test_platform_may_reclaim_a_machine_it_opened():
    service = _service()
    owner = uuid.uuid4()
    device_id = await _enrol(service, Supply.cloud, owner)

    await service.delete_platform_provisioned(device_id, actor_user_id=owner)

    assert await service.get_device(device_id) is None


async def test_platform_reclaim_of_a_self_hosted_machine_raises_and_keeps_it():
    """The invariant. It RAISES rather than skipping, because a reclaim path added
    later that forgot to ask would otherwise delete someone else's machine
    silently — and a silent wrong disposal is exactly what #282 is about."""
    service = _service()
    owner = uuid.uuid4()
    device_id = await _enrol(service, Supply.self_hosted, owner)

    with pytest.raises(ForbiddenError):
        await service.delete_platform_provisioned(device_id, actor_user_id=owner)

    assert await service.get_device(device_id) is not None


async def test_the_owner_may_always_remove_their_own_machine():
    """The human's door stays open whatever the supply — 「我不想再借给平台了」is
    not a platform reclaim, and the invariant above must not block it."""
    service = _service()
    owner = uuid.uuid4()
    device_id = await _enrol(service, Supply.self_hosted, owner)

    await service.delete_owned(device_id, actor_user_id=owner)

    assert await service.get_device(device_id) is None


async def test_an_unclassified_device_reads_as_the_safe_value_on_each_axis():
    """Each dataclass default is the SAFE reading of ITS OWN axis — and the two axes
    are safe in OPPOSITE directions, so an unclassified device is not "host + cloud":

      * supply → self_hosted: the destroy decision reads this field, and the safe
        reading is "someone else's machine", under which the platform destroys
        nothing;
      * visibility → isolated: the destroy decision does NOT read this field, so the
        safe reading is the ACCESS-safe one (boxed), never whole-machine-visible by
        omission (#358). `host` is 申请制.
    """
    from app.domain.device.repository import Device

    device = Device(
        device_id="d1",
        name="n",
        token="t",
        owner_user_id=1,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert device.supply is Supply.self_hosted
    assert device.visibility is Visibility.isolated


def test_the_default_visibility_is_one_that_can_actually_run():
    """The default must never name a 档 with no transport.

    This is the shape of the bug it replaces: the market catalogue declared
    `isolated` the default while `resolve_pinned_device` bound `host`, so the
    picker told a person their topic was boxed and every topic in fact had
    whole-machine access. Deriving the default from `has_runnable_transport`
    makes that combination unrepresentable."""
    from app.domain.device.supply import default_visibility, has_runnable_transport

    assert has_runnable_transport(default_visibility())


def test_the_default_is_the_most_conservative_runnable_visibility():
    """Given a choice, the default is the SMALLEST blast radius that works —
    so when #358 step 2 gives `isolated` a transport, the default moves to it
    without anyone editing a second place."""
    from app.domain.device import supply as supply_mod
    from app.domain.device.supply import Visibility, default_visibility

    assert default_visibility() is Visibility.host  # today: isolated has no transport

    original = supply_mod.has_runnable_transport
    try:
        supply_mod.has_runnable_transport = lambda _v: True
        assert default_visibility() is Visibility.isolated
    finally:
        supply_mod.has_runnable_transport = original


def test_the_catalogue_default_is_the_one_the_resolver_would_bind():
    """The picker and the resolver must agree, and exactly one 档 is the default.

    Nothing else in the suite covers the pair — each side was individually
    correct and they still contradicted each other in production."""
    from app.domain.agent.market import visibility_listings
    from app.domain.device.supply import default_visibility

    listings = visibility_listings()
    defaults = [entry for entry in listings if entry.default]
    assert len(defaults) == 1
    assert defaults[0].id == default_visibility().value
    # And a default nobody can run is the contradiction itself.
    assert defaults[0].available is True
