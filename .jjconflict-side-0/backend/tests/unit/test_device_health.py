"""判定「这台机器废了」 (#186).

A turn that fails because the *machine* is unusable — disk full, host not
answering — used to be the one failure the platform recognised perfectly and then
did nothing about. These tests pin the judgement: what counts as evidence against a
machine, what clears it, and what happens to a machine that has been judged.

Behaviour only — driven through ``DeviceService`` with the in-memory repo, no DB.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent.platform_failures import (
    HOST_UNREACHABLE,
    RUNTIME_IMAGE_MISSING,
    STORAGE_EXHAUSTED,
)
from app.domain.device.health import DEFAULT_QUARANTINE
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply, Visibility

OWNER = 1
T0 = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


class Clock:
    """A hand-wound clock, so a 30-minute cooldown costs no wall time."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def _service(clock: Clock | None = None) -> DeviceService:
    return DeviceService(InMemoryDeviceRepository(), now=clock or Clock())


async def _device_on_project(
    service: DeviceService, project_id: uuid.UUID, name: str
) -> str:
    code = await service.start(name)
    # `supply`/`visibility` are required with no default (#282 决定 2 / #358): every
    # enrolment site states its own answer. These tests enrol a self-hosted box and
    # exercise health/quarantine, which read NEITHER field — both are stated, not
    # asserted, so they take the conservative self-hosted defaults.
    device = await service.approve(
        code,
        owner_user_id=OWNER,
        supply=Supply.self_hosted,
        visibility=Visibility.isolated,
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


def _online(*ids: str):
    live = set(ids)
    return lambda device_id: device_id in live


# --- what counts as evidence -------------------------------------------------


async def test_one_failure_is_a_hiccup_two_in_a_row_is_a_dead_machine():
    service = _service()
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    first = await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    assert not first.quarantined, "one failure must not take a machine out of rotation"
    assert await service.healthy_devices_for_project(project, _online(dev)) != []

    second = await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    assert second.quarantined
    assert second.consecutive_failures == 2
    assert await service.healthy_devices_for_project(project, _online(dev)) == []


async def test_a_successful_turn_in_between_clears_the_streak():
    service = _service()
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    await service.record_host_success(dev)
    verdict = await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)

    assert not verdict.quarantined, "'consecutive' must mean consecutive"
    assert verdict.consecutive_failures == 1
    assert await service.healthy_devices_for_project(project, _online(dev)) != []


async def test_two_different_failures_are_two_accidents_not_one_dying_machine():
    service = _service()
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    verdict = await service.record_host_failure(dev, HOST_UNREACHABLE.code)

    assert not verdict.quarantined
    assert verdict.consecutive_failures == 1


def test_only_machine_shaped_failures_can_indict_a_machine():
    """A missing runtime image is a registry problem: it hits every machine at once
    and would follow the topic wherever it went, so it must never count against the
    box it happened to land on. This flag is the gate — the swap path refuses any
    failure without it (see ``test_host_swap``)."""
    assert STORAGE_EXHAUSTED.host_scoped, "a full disk belongs to the machine"
    assert HOST_UNREACHABLE.host_scoped, "an unreachable host IS the machine"
    assert not RUNTIME_IMAGE_MISSING.host_scoped


# --- what a judged machine means for placement -------------------------------


async def test_a_quarantined_machine_is_not_handed_new_topics():
    service = _service()
    project = uuid.uuid4()
    sick = await _device_on_project(service, project, "sick")
    well = await _device_on_project(service, project, "well")

    await service.record_host_failure(sick, STORAGE_EXHAUSTED.code)
    await service.record_host_failure(sick, STORAGE_EXHAUSTED.code)

    healthy = await service.healthy_devices_for_project(project, _online(sick, well))
    assert [d.device_id for d in healthy] == [well]


async def test_quarantine_lapses_on_its_own_so_a_machine_can_come_back():
    clock = Clock()
    service = _service(clock)
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    assert await service.healthy_devices_for_project(project, _online(dev)) == []

    clock.advance(DEFAULT_QUARANTINE + timedelta(minutes=1))
    healthy = await service.healthy_devices_for_project(project, _online(dev))
    assert [d.device_id for d in healthy] == [dev], (
        "a full disk heals on its own; the machine must return without a human"
    )


async def test_an_offline_machine_is_never_offered_quarantined_or_not():
    service = _service()
    project = uuid.uuid4()
    await _device_on_project(service, project, "box")

    assert await service.healthy_devices_for_project(project, _online()) == []


async def test_a_strike_from_long_ago_does_not_join_todays_failure():
    """The streak has to be recent as well as unbroken.

    Clearing it on a good turn is best effort — the turn layer only pays for that
    clear when the same process saw the machine fail, so a strike recorded before a
    restart has nobody left to clear it. Without ageing, that orphan would sit
    there indefinitely and quarantine the machine on the next unrelated failure,
    however many good turns ran in between.
    """
    clock = Clock()
    service = _service(clock)
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    clock.advance(timedelta(hours=2))
    verdict = await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)

    assert not verdict.quarantined, "two hours apart is two incidents, not a streak"
    assert verdict.consecutive_failures == 1
    assert await service.healthy_devices_for_project(project, _online(dev)) != []


async def test_two_failures_close_together_still_indict_the_machine():
    """The ageing rule must not blunt the standard it protects."""
    clock = Clock()
    service = _service(clock)
    project = uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)
    clock.advance(timedelta(minutes=1))
    verdict = await service.record_host_failure(dev, STORAGE_EXHAUSTED.code)

    assert verdict.quarantined
    assert await service.healthy_devices_for_project(project, _online(dev)) == []
