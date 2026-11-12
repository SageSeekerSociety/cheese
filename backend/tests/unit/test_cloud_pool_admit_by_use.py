"""A host past its reserved slots takes another sandbox only on what it reads.

The reserved slots (`models.capacity`) count every sandbox at its full memory
limit: four on the default host. Past them, `reaper.memory_admits` lets a host
take one more while its cores and disk allow (`models.hard_capacity`) and its
last reading — recent, under little memory pressure, with room for one more
sandbox at its limit and some headroom — says the memory is there. Sandboxes
placed after that reading count at their full limit, since it cannot have
seen them.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.agent import sandbox_image
from app.domain.machine.models import CloudHost, HostReading, capacity, hard_capacity
from app.domain.machine.reaper import accepting, memory_admits

GIB = 1 << 30
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_admit_by_use", True)
    monkeypatch.setattr(settings, "cloud_pool_admit_reading_s", 300)
    monkeypatch.setattr(settings, "cloud_pool_admit_pressure", 8.0)
    monkeypatch.setattr(settings, "cloud_pool_admit_headroom_mb", 2048)
    monkeypatch.setattr(settings, "cloud_sandbox_memory_mb", 3072)
    monkeypatch.setattr(settings, "cloud_host_slots_per_core", 2)
    monkeypatch.setattr(settings, "cloud_host_swap_mb", 8192)
    monkeypatch.setattr(settings, "cloud_host_disk_reserve_gb", 10)
    monkeypatch.setattr(settings, "cloud_sandbox_disk_gb", 4)


def host() -> CloudHost:
    """The default pool host: four cores, 16 GiB, a 64 GB data disk."""
    return CloudHost(
        id=uuid.uuid4(), cores=4, memory_mb=16384, disk_gb=40, sandbox_disk_gb=64
    )


def reading(
    of: CloudHost, *, available_gib: float, pressure: float = 0.0, age_s: int = 60
) -> HostReading:
    return HostReading(
        host_id=of.id,
        read_at=NOW - timedelta(seconds=age_s),
        disk_bytes=64 * GIB,
        disk_free_bytes=40 * GIB,
        memory_bytes=16 * GIB,
        memory_available_bytes=int(available_gib * GIB),
        memory_pressure_some_avg60=pressure,
    )


def test_the_default_host_reserves_four_slots_and_holds_eight_at_most():
    """Four at 3 GiB each fit the 12 GiB the host gives its sandboxes; its
    four cores at two each, and its disk, allow eight."""
    one = host()
    assert capacity(one) == 4
    assert hard_capacity(one) == 8


@pytest.mark.parametrize("prepared", [False, True])
def test_an_enrolled_host_takes_new_sandboxes_only_with_the_default_image(prepared):
    one = host()
    one.device_id = "test-cloud-device"
    one.sandbox_image = sandbox_image.default_image()["reference"] if prepared else None
    assert accepting(one) is prepared


def test_a_full_host_with_memory_to_spare_takes_one_more():
    one = host()
    assert memory_admits(one, {one.id: 4}, reading(one, available_gib=12), 0, NOW)


def test_it_takes_none_past_its_cores():
    one = host()
    assert not memory_admits(one, {one.id: 8}, reading(one, available_gib=12), 0, NOW)


def test_it_takes_none_under_memory_pressure():
    one = host()
    assert not memory_admits(
        one, {one.id: 4}, reading(one, available_gib=12, pressure=8.0), 0, NOW
    )


def test_it_keeps_room_for_one_sandbox_at_its_limit_and_the_headroom():
    """3 GiB for the newcomer and 2 GiB to spare: 5 GiB available admits, a
    little less does not."""
    one = host()
    load = {one.id: 4}
    assert memory_admits(one, load, reading(one, available_gib=5), 0, NOW)
    assert not memory_admits(one, load, reading(one, available_gib=4.9), 0, NOW)


def test_sandboxes_placed_after_the_reading_count_at_their_limit():
    """The reading said 8 GiB were free; a sandbox placed since may take 3 of
    them, so one more fits and a second does not."""
    one = host()
    load = {one.id: 5}
    assert memory_admits(one, load, reading(one, available_gib=8), 1, NOW)
    assert not memory_admits(one, load, reading(one, available_gib=8), 2, NOW)


def test_no_reading_or_an_old_one_admits_nothing():
    one = host()
    load = {one.id: 4}
    assert not memory_admits(one, load, None, 0, NOW)
    assert not memory_admits(
        one, load, reading(one, available_gib=12, age_s=301), 0, NOW
    )


def test_a_reading_without_its_memory_numbers_admits_nothing():
    one = host()
    blank = reading(one, available_gib=12)
    blank.memory_pressure_some_avg60 = None
    assert not memory_admits(one, {one.id: 4}, blank, 0, NOW)


def test_turned_off_the_reserved_slots_are_all_there_is(monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_admit_by_use", False)
    one = host()
    assert not memory_admits(one, {one.id: 4}, reading(one, available_gib=12), 0, NOW)
