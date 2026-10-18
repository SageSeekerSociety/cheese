"""The pool's cap counts whole VMs, and a VM keeps a longer keep-alive."""

import asyncio
from datetime import datetime

import pytest

from app.core.config import Settings, settings
from app.domain.agent.environment import STANDARD, VM, spec_of
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


def _room_to_grow(hosts, monkeypatch, *, whole_machine: bool) -> None:
    monkeypatch.setattr(settings, "cloud_pool_max_hosts", 12)
    asyncio.run(
        HostPool._require_room_to_grow(_PoolWith(hosts), whole_machine=whole_machine)
    )


def test_a_sandbox_keeps_its_background_command_for_fifteen_minutes():
    assert Settings.model_fields["cloud_sandbox_background_cap_s"].default == 900


def test_a_whole_vm_keeps_its_background_cap():
    """A VM's idle release outlasts a sandbox's cap, so a cap as short as the
    sandbox's would hold nothing up; the VM's own cap is an hour."""
    assert Settings.model_fields["cloud_vm_background_cap_s"].default == 3600
    vm, sandbox = spec_of(VM), spec_of(STANDARD)
    assert vm is not None and sandbox is not None
    assert vm.background_cap_s == 3600
    assert sandbox.background_cap_s == 900
    assert vm.background_cap_s > vm.idle_s


def test_whole_machines_count_toward_the_pool(monkeypatch):
    """A whole VM holds no sandbox slot but still takes one of the places:
    ten sandbox hosts and two VMs fill the pool for either kind of request."""
    hosts = [_host() for _ in range(10)] + [_host(whole_machine=True) for _ in range(2)]
    for whole_machine in (False, True):
        with pytest.raises(CloudPoolFull):
            _room_to_grow(hosts, monkeypatch, whole_machine=whole_machine)


def test_eleven_hosts_leave_a_place(monkeypatch):
    hosts = [_host() for _ in range(10)] + [_host(whole_machine=True)]
    _room_to_grow(hosts, monkeypatch, whole_machine=True)
