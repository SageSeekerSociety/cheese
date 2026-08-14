"""A machine the provider destroyed must stop being reported as running.

Observed live on 2026-08-02: three machines were `running` and enrolled in this
table while MicroCloud returned 404 for every one of them. A settled machine was
never asked about again, so the books could not correct themselves — and
`provision()` counts those rows against the per-project limit, so a project
whose machines are gone upstream could never get another one.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.services import MachineService


class _Repo:
    """Just enough of the repository to observe what the service decides."""

    def __init__(self, machines):
        self.machines = list(machines)
        self.deleted = []

    async def list_for_project(self, project_id):
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
        id=uuid.uuid4(),
        machine_id=207,
        status=MachineStatus.running,
        ai_status=AiStatus.ready,
        ai_mode="newapi",
        ip="192.168.31.6",
        device_id="dev-1",
        last_seen_at=None,
        owner_user_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _service(repo, client) -> MachineService:
    service = MachineService.__new__(MachineService)
    service._repo = repo  # type: ignore[attr-defined]
    service._client = client  # type: ignore[attr-defined]
    return service


@pytest.mark.anyio
async def test_a_machine_the_provider_forgot_stops_being_reported():
    """The failure this whole change exists for: settled means never re-checked,
    so a 404 upstream never reaches our books."""
    repo = _Repo([_machine()])
    client = _Client(None)  # MicroCloud 404 -> get_machine returns None
    alive = await _service(repo, client).list_for_project(uuid.uuid4())

    assert client.calls == 1, "a settled machine was never re-checked"
    assert alive == [], "a machine MicroCloud has forgotten was reported as alive"
    assert len(repo.deleted) == 1, "its row still occupies one of the project's slots"


@pytest.mark.anyio
async def test_a_recently_checked_machine_is_not_re_fetched():
    """This sits on a request path. Asking the provider on every read is its own
    outage — the guard must be bounded, not unconditional."""
    fresh = _machine(last_seen_at=datetime.now(UTC) - timedelta(seconds=5))
    repo = _Repo([fresh])
    client = _Client(None)
    alive = await _service(repo, client).list_for_project(uuid.uuid4())

    assert client.calls == 0
    assert alive == [fresh]


@pytest.mark.anyio
async def test_an_unreachable_provider_does_not_condemn_a_healthy_machine():
    """A blip is not evidence the machine is broken. Reporting `unknown` here
    would trade one silent failure for a visible-but-wrong one, and would make
    every later read re-fetch it."""
    from app.domain.machine.microcloud import MicroCloudError

    machine = _machine(last_seen_at=datetime.now(UTC) - timedelta(hours=2))
    repo = _Repo([machine])
    client = _Client(MicroCloudError("connection refused"))
    alive = await _service(repo, client).list_for_project(uuid.uuid4())

    assert alive == [machine]
    assert machine.status == MachineStatus.running
    assert machine.ai_status == AiStatus.ready
    # The attempt is still recorded, or a provider outage puts a provider
    # timeout on every single read.
    assert machine.last_seen_at is not None
    assert (datetime.now(UTC) - machine.last_seen_at).total_seconds() < 5


@pytest.mark.anyio
async def test_a_still_provisioning_machine_reports_unknown_when_unreachable():
    """Unchanged behaviour, asserted so the narrowing above cannot silently
    widen: for a machine we were waiting on, `unknown` IS the honest answer."""
    from app.domain.machine.microcloud import MicroCloudError

    machine = _machine(
        status=MachineStatus.provisioning, ai_status=AiStatus.provisioning
    )
    repo = _Repo([machine])
    client = _Client(MicroCloudError("connection refused"))
    await _service(repo, client).list_for_project(uuid.uuid4())

    assert machine.status == MachineStatus.unknown


# --- enrolment waits for the AI channel to settle ---------------------------
# Found live on 2026-08-14: machine 472 was enrolled while still at
# `newapi/ready`, switched to ccproxy one step later, and can never have its
# ccproxy identity read again — `mark_enrolled` erases the bootstrap key, so
# that ssh session was the only chance. The order and the wait are both load
# bearing, and neither leaves a trace when it regresses: the machine enrols
# fine, the identity is simply absent forever.


def test_the_ai_channel_is_converged_before_enrolment_not_after():
    """Source order, because the failure it prevents is invisible at runtime:
    every machine still enrols, and only the identity silently goes missing."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "app" / "domain" / "machine" / "runner.py"
    )
    text = source.read_text()
    reconcile = text.index("reconcile_ai_mode()")
    enroll = text.index("enroll_pending()")
    assert reconcile < enroll, "reconcile must run before enrolment"


async def test_enrolment_asks_only_for_machines_whose_channel_has_settled(
    monkeypatch,
):
    """The gate has to reach the QUERY, not just exist: enrolment is the one ssh
    session, and a machine picked up before its channel settled loses its ccproxy
    identity permanently. The grace is passed too — a machine whose channel never
    settles must still become usable compute, just on the shared identity."""
    from app.core.config import settings as app_settings
    from app.domain.machine import services as machine_services

    monkeypatch.setattr(app_settings, "microcloud_ai_mode", "ccproxy")
    seen: dict = {}

    class _EnrolRepo:
        async def list_awaiting_enrollment(self, limit, **kwargs):
            seen.update(kwargs)
            return []

    service = MachineService.__new__(MachineService)
    service._repo = _EnrolRepo()  # type: ignore[attr-defined]

    await machine_services.MachineService.enroll_pending(service)

    assert seen["desired_ai_mode"] == "ccproxy"
    assert seen["settle_cutoff"] is not None, "an unbounded wait bricks a machine"
