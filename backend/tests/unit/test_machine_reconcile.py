"""A host the provider destroyed must stop being counted as part of the pool.

Observed live on 2026-08-02: three machines were `running` and enrolled in this
table while MicroCloud returned 404 for every one of them. A settled machine was
never asked about again, so the books could not correct themselves — and the
pool would place sessions on hosts that are gone.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.services import HostPool


class _Repo:
    """Just enough of the repository to observe what the service decides."""

    def __init__(self, machines):
        self.machines = list(machines)
        self.deleted = []

    async def list_due(self, limit, *, seen_before):
        return list(self.machines)

    async def set_state(
        self, machine, *, status, ip, ai_mode=None, ai_status=None, seen_at=None
    ):
        machine.status = status
        if ip:
            machine.ip = ip
        if ai_mode is not None:
            machine.ai_mode = ai_mode
        if ai_status is not None:
            machine.ai_status = ai_status
        if seen_at is not None:
            machine.last_seen_at = seen_at
        return machine

    async def touch_seen(self, machine, *, when):
        machine.last_seen_at = when
        return machine

    async def delete(self, machine):
        self.deleted.append(machine)
        self.machines.remove(machine)


class _Client:
    def __init__(self, answer):
        self._answer = answer
        self.calls = 0

    async def get_machine(self, machine_id):
        self.calls += 1
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _machine(**kw):
    base = dict(
        warm_claim_pending=False,
        id=uuid.uuid4(),
        machine_id=207,
        status=MachineStatus.running,
        ai_status=AiStatus.disabled,
        ai_mode="none",
        ip="192.168.31.6",
        device_id="dev-1",
        last_seen_at=None,
        owner_user_id=None,
        failed_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Devices:
    async def get_device(self, device_id):
        return None


def _service(repo, client) -> HostPool:
    service = HostPool.__new__(HostPool)
    service._repo = repo  # type: ignore[attr-defined]
    service._client = client  # type: ignore[attr-defined]
    service._devices = _Devices()  # type: ignore[attr-defined]
    return service


@pytest.mark.anyio
async def test_a_machine_the_provider_forgot_stops_being_reported():
    """The failure this whole change exists for: settled means never re-checked,
    so a 404 upstream never reaches our books."""
    repo = _Repo([_machine()])
    client = _Client(None)  # MicroCloud 404 -> get_machine returns None
    service = _service(repo, client)
    await service.refresh_due()

    assert client.calls == 1, "a settled machine was never re-checked"
    assert repo.machines == [], "a host MicroCloud has forgotten is still in the pool"


@pytest.mark.anyio
async def test_an_unreachable_provider_does_not_condemn_a_healthy_machine():
    """A blip is not evidence the machine is broken. Reporting `unknown` here
    would trade one silent failure for a visible-but-wrong one."""
    from app.domain.machine.microcloud import MicroCloudError

    machine = _machine(last_seen_at=datetime.now(UTC) - timedelta(hours=2))
    repo = _Repo([machine])
    client = _Client(MicroCloudError("connection refused"))
    await _service(repo, client).refresh_due()

    assert machine.status == MachineStatus.running
    assert machine.ai_status == AiStatus.disabled
    # The attempt is still recorded, or a provider outage makes the sweep spend
    # every tick on the same machines.
    assert machine.last_seen_at is not None
    assert (datetime.now(UTC) - machine.last_seen_at).total_seconds() < 5


@pytest.mark.anyio
async def test_a_still_provisioning_machine_reports_unknown_when_unreachable():
    """Unchanged behaviour, asserted so the narrowing above cannot silently
    widen: for a machine we were waiting on, `unknown` IS the honest answer."""
    from app.domain.machine.microcloud import MicroCloudError

    machine = _machine(status=MachineStatus.provisioning, ai_status=AiStatus.unknown)
    repo = _Repo([machine])
    client = _Client(MicroCloudError("connection refused"))
    await _service(repo, client).refresh_due()

    assert machine.status == MachineStatus.unknown


async def test_the_sweep_refreshes_before_it_decides(monkeypatch):
    """Enrolment decides on state only this refresh updates. Machine 473 sat
    unused for 13 minutes on 2026-08-14 while MicroCloud had it settled the
    whole time — the sweep was deciding on a value nothing in it refreshed."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "app" / "domain" / "machine" / "runner.py"
    )
    text = source.read_text()
    assert text.index("refresh_due()") < text.index("enroll_pending()")


async def test_a_provider_outage_does_not_stop_the_refresh_sweep():
    """One unreachable machine must not cost the others their turn: the sweep is
    the only thing that unsticks a machine, so it has to keep going."""
    from app.domain.machine.microcloud import MicroCloudError

    class _Repo:
        async def list_due(self, limit, *, seen_before):
            return [
                SimpleNamespace(hostname=name, status=MachineStatus.starting)
                for name in ("a", "b")
            ]

    service = HostPool.__new__(HostPool)
    service._repo = _Repo()  # type: ignore[attr-defined]
    seen: list[str] = []

    async def _refresh(machine):
        seen.append(machine.hostname)
        if machine.hostname == "a":
            raise MicroCloudError("provider down")

    service.refresh = _refresh  # type: ignore[method-assign]

    assert await HostPool.refresh_due(service) == 2
    assert seen == ["a", "b"]
