"""The cloud pool's cap counts sandbox hosts only, and idle sandboxes go sooner."""

import asyncio
from datetime import datetime

import pytest

from app.core.config import Settings, settings
from app.domain.machine.models import CloudHost
from app.domain.machine.services import CloudPoolFull, HostPool


def _host(*, whole_machine: bool = False) -> CloudHost:
    return CloudHost(whole_machine=whole_machine, released_at=None)


class _PoolWith:
    """What ``_require_room_to_grow`` reads: the live hosts, no failing provider."""

    def __init__(self, hosts: list[CloudHost]) -> None:
        self._hosts = hosts
        self._repo = self

    async def _provider_failing(self, now: datetime) -> int:
        return 0

    async def live(self) -> list[CloudHost]:
        return self._hosts


def _room_to_grow(hosts: list[CloudHost], monkeypatch) -> None:
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 10)
    asyncio.run(HostPool._require_room_to_grow(_PoolWith(hosts), whole_machine=False))


def test_whole_machines_do_not_fill_the_pool(monkeypatch):
    hosts = [_host() for _ in range(8)] + [_host(whole_machine=True) for _ in range(2)]
    _room_to_grow(hosts, monkeypatch)


def test_ten_ordinary_hosts_still_fill_the_pool(monkeypatch):
    with pytest.raises(CloudPoolFull):
        _room_to_grow([_host() for _ in range(10)], monkeypatch)


def test_idle_sandboxes_are_reclaimed_after_three_minutes():
    assert Settings.model_fields["cloud_sandbox_idle_stop_s"].default == 180
    assert Settings.model_fields["cloud_sandbox_background_cap_s"].default == 900


def test_a_whole_machine_request_still_counts_the_machines(monkeypatch):
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 10)
    hosts = [_host() for _ in range(8)] + [_host(whole_machine=True) for _ in range(2)]
    with pytest.raises(CloudPoolFull):
        asyncio.run(
            HostPool._require_room_to_grow(_PoolWith(hosts), whole_machine=True)
        )
