"""Project machine provisioning against a fake MicroCloud.

The provider is asynchronous and remote, so what matters here is behaviour at
the seams: a spec that the granted offering cannot honour, a provider that stops
answering, a machine that vanished, and the cross-project addressing guard.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.domain.machine.microcloud import MicroCloudError
from app.domain.machine.models import AiStatus, MachineStatus
from app.domain.machine.services import MachineService, customer_ref, derive_hostname

pytestmark = pytest.mark.anyio


OFFERING = {
    "id": 1,
    "status": "active",
    "machineTypeName": "standard",
    "zoneName": "cn-east",
    "templateName": "debian13",
    "coresMin": 1,
    "coresMax": 8,
    "memoryMbMin": 512,
    "memoryMbMax": 16384,
    "diskGbMin": 10,
    "diskGbMax": 100,
}


class FakeMicroCloud:
    """Stands in for the remote control plane; records what it was asked."""

    configured = True

    def __init__(self, offerings=None):
        self._offerings = OFFERING if offerings is None else offerings
        self.created: list[dict] = []
        self.deleted: list[int] = []
        self.ai_switches: list[tuple[int, str]] = []
        self.topups: list[tuple[int, float]] = []
        self.machines: dict[int, dict] = {}
        self._next_id = 100
        self.customers: dict[str, dict] = {}
        self.accounts: dict[tuple[int, str], dict] = {}

    async def list_offerings(self):
        if self._offerings is None:
            return []
        return (
            [self._offerings] if isinstance(self._offerings, dict) else self._offerings
        )

    async def find_customer(self, external_ref):
        return self.customers.get(external_ref)

    async def create_customer(self, external_ref):
        customer = {"id": 7, "externalRef": external_ref}
        self.customers[external_ref] = customer
        return customer

    async def find_account(self, customer_id, name):
        return self.accounts.get((customer_id, name))

    async def create_account(self, customer_id, name):
        account = {"id": 9, "customerId": customer_id, "name": name, "balance": 0}
        self.accounts[(customer_id, name)] = account
        return account

    async def topup(self, account_id, amount, remark=""):
        self.topups.append((account_id, amount))
        return {"id": account_id, "balance": amount}

    async def create_machine(self, body):
        self.created.append(body)
        self._next_id += 1
        machine = {
            "id": self._next_id,
            "status": "provisioning",
            "ip": None,
            "aiMode": "newapi",
            "aiStatus": "provisioning",
            **body,
        }
        self.machines[self._next_id] = machine
        return machine

    async def get_machine(self, machine_id):
        return self.machines.get(machine_id)

    async def delete_machine(self, machine_id):
        self.deleted.append(machine_id)
        self.machines.pop(machine_id, None)

    async def switch_ai(self, machine_id, mode):
        self.ai_switches.append((machine_id, mode))
        machine = self.machines.get(machine_id)
        if machine is not None:
            machine["aiMode"] = mode
            machine["aiStatus"] = "provisioning"
        # The real control plane answers a switch in UPPERCASE.
        return {
            "machineId": machine_id,
            "aiMode": mode.upper(),
            "aiStatus": "PROVISIONING",
        }


class FakeRepo:
    """In-memory stand-in for ProjectMachineRepository."""

    def __init__(self):
        self.rows: list[SimpleNamespace] = []

    async def add(self, **kwargs):
        kwargs.setdefault("last_seen_at", None)
        row = SimpleNamespace(id=uuid.uuid4(), device_id=None, **kwargs)
        self.rows.append(row)
        return row

    async def get(self, row_id):
        return next((r for r in self.rows if r.id == row_id), None)

    async def list_for_project(self, project_id):
        return [r for r in self.rows if r.project_id == project_id]

    async def list_ai_mode_mismatch(self, desired, limit):
        return [
            r
            for r in self.rows
            if r.status == MachineStatus.running
            and r.ai_status == AiStatus.ready
            and r.ai_mode != desired
        ][:limit]

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
        self.rows.remove(machine)


class FakeDevices:
    def __init__(self):
        self.devices: dict[str, SimpleNamespace] = {}
        self.deleted: list[str] = []

    async def get_device(self, device_id):
        return self.devices.get(device_id)

    async def delete_owned(self, device_id, *, actor_user_id):
        device = self.devices[device_id]
        assert device.owner_user_id == actor_user_id
        self.deleted.append(device_id)
        del self.devices[device_id]


_UNSET = object()


def build_service(client=None, project=_UNSET, repo=None):
    service = MachineService.__new__(MachineService)
    service._session = None
    service._client = client or FakeMicroCloud()
    service._repo = repo or FakeRepo()
    service._devices = FakeDevices()
    if project is _UNSET:
        project = SimpleNamespace(id=uuid.uuid4(), name="Cheese 自建")
    service._projects = SimpleNamespace(get=_returning(project))
    return service


def _returning(value):
    async def _get(_):
        return value

    return _get


async def test_provision_clamps_a_spec_the_offering_cannot_honour():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()

    await service.provision(
        project_id=project_id, requested_by="andy", cores=64, memory_mb=1, disk_gb=9999
    )

    body = client.created[0]
    assert body["cores"] == OFFERING["coresMax"]
    assert body["memoryMb"] == OFFERING["memoryMbMin"]
    assert body["diskGb"] == OFFERING["diskGbMax"]


async def test_provision_bills_the_project_not_the_person():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()

    await service.provision(project_id=project_id, requested_by="andy")

    assert customer_ref(project_id) in client.customers
    assert client.topups, "a fresh account must be funded before it is charged"


async def test_provision_reuses_the_projects_existing_account():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()

    await service.provision(project_id=project_id, requested_by="andy")
    first_topups = len(client.topups)
    client.accounts[(7, "compute")]["balance"] = 10_000
    await service.provision(project_id=project_id, requested_by="andy")

    assert len(client.customers) == 1
    assert len(client.topups) == first_topups, (
        "a funded account must not be topped up again"
    )


async def test_provision_refuses_past_the_per_project_ceiling():
    from app.core.config import settings

    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()

    for _ in range(settings.microcloud_max_machines_per_project):
        await service.provision(project_id=project_id, requested_by="andy")

    with pytest.raises(ValidationError):
        await service.provision(project_id=project_id, requested_by="andy")


async def test_provision_rejects_an_unknown_project():
    service = build_service(project=None)
    with pytest.raises(NotFoundError):
        await service.provision(project_id=uuid.uuid4(), requested_by="andy")


async def test_provision_explains_a_tenant_with_no_offering():
    service = build_service(FakeMicroCloud(offerings=[]))
    with pytest.raises(ValidationError):
        await service.provision(project_id=uuid.uuid4(), requested_by="andy")


async def test_unreachable_provider_reports_unknown_not_error():
    class Down(FakeMicroCloud):
        async def get_machine(self, machine_id):
            raise MicroCloudError("boom")

    client = Down()
    service = build_service(client)
    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    refreshed = await service.refresh(machine)
    assert refreshed.status == MachineStatus.unknown, (
        "a provider outage must be distinguishable from a machine that failed"
    )


async def test_a_machine_that_vanished_upstream_reads_as_deleted():
    client = FakeMicroCloud()
    service = build_service(client)
    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    client.machines.clear()
    refreshed = await service.refresh(machine)
    assert refreshed.status == MachineStatus.deleted


async def test_the_platform_key_never_displaces_the_humans():
    """Enrolling a machine must not cost the person their way into it.

    MicroCloud takes a single sshPubkey field, so the platform's bootstrap key
    and the human's key go in together — authorized_keys is one key per line.
    """
    client = FakeMicroCloud()
    service = build_service(client)

    await service.provision(
        project_id=uuid.uuid4(),
        requested_by="andy",
        ssh_pubkey="  ssh-ed25519 HUMANKEY andy@laptop  ",
    )

    authorized = client.created[0]["sshPubkey"].splitlines()
    assert "ssh-ed25519 HUMANKEY andy@laptop" in authorized
    assert any("cheese-bootstrap" in line for line in authorized)
    assert len(authorized) == 2


async def test_a_machine_asked_for_without_a_key_still_gets_the_platforms():
    # Otherwise the platform could never enroll it, and the machine would be
    # provisioned compute nobody — human or agent — can reach.
    client = FakeMicroCloud()
    service = build_service(client)

    await service.provision(project_id=uuid.uuid4(), requested_by="andy")
    assert "cheese-bootstrap" in client.created[0]["sshPubkey"]


async def test_reading_a_project_picks_up_progress_made_while_nobody_looked():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(project_id=project_id, requested_by="andy")

    client.machines[machine.machine_id].update(
        status="running", ip="10.0.0.5", aiStatus="ready"
    )
    await service.list_for_project(project_id)

    assert machine.status == MachineStatus.running
    assert machine.ip == "10.0.0.5"
    assert machine.ai_status == AiStatus.ready


async def test_a_known_ip_is_never_blanked_by_a_later_read():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(project_id=project_id, requested_by="andy")

    client.machines[machine.machine_id].update(status="running", ip="10.0.0.5")
    await service.refresh(machine)
    client.machines[machine.machine_id].update(status="stopping", ip=None)
    await service.refresh(machine)

    assert machine.ip == "10.0.0.5"


def test_hostname_is_dns_safe_and_identifies_the_project():
    project_id = uuid.UUID("de808b13-ffd2-4b8a-9d1d-fba7babe389f")
    name = derive_hostname("Cheese 自建 / 2026!", project_id, 1)
    # Non-ASCII and punctuation collapse away; what survives still names the
    # project, its id, and which machine this is.
    assert name == "cheese-2026-de808b-1"
    assert all(c.isalnum() or c == "-" for c in name)


def test_hostname_survives_a_name_with_nothing_usable_in_it():
    assert derive_hostname("自建", uuid.UUID(int=0), 2).startswith("project-")


async def test_a_running_machine_with_ai_still_provisioning_keeps_being_polled():
    """The regression this second lifecycle introduces.

    MicroCloud reports `running` while it is still wiring the machine's Claude
    Code. Polling on the machine status alone stops right there, freezing
    ai_status at `provisioning` forever — a machine that cannot run a turn would
    read as finished.
    """
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(project_id=project_id, requested_by="andy")

    client.machines[machine.machine_id].update(status="running", ip="10.0.0.5")
    await service.list_for_project(project_id)
    assert machine.status == MachineStatus.running
    assert machine.ai_status == AiStatus.provisioning

    client.machines[machine.machine_id]["aiStatus"] = "ready"
    await service.list_for_project(project_id)
    assert machine.ai_status == AiStatus.ready


async def test_polling_stops_once_both_lifecycles_settle():
    class Counting(FakeMicroCloud):
        def __init__(self):
            super().__init__()
            self.reads = 0

        async def get_machine(self, machine_id):
            self.reads += 1
            return self.machines.get(machine_id)

    client = Counting()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(project_id=project_id, requested_by="andy")

    client.machines[machine.machine_id].update(
        status="running", ip="10.0.0.5", aiStatus="ready"
    )
    await service.list_for_project(project_id)
    settled = client.reads
    await service.list_for_project(project_id)
    assert client.reads == settled

    # …but "settled" is not "never asked again". It used to be, and that is how
    # three machines destroyed upstream stayed `running` in this table while
    # MicroCloud 404'd every one of them — and kept occupying the project's
    # slots. Once the last answer is old enough, it is re-checked.
    for row in service._repo.rows:  # type: ignore[attr-defined]
        row.last_seen_at = datetime.now(UTC) - timedelta(hours=1)
    await service.list_for_project(project_id)
    assert client.reads > settled


async def test_ai_accounts_are_named_rather_than_left_to_the_default():
    client = FakeMicroCloud()
    service = build_service(client)
    await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    body = client.created[0]
    # MicroCloud would default both to accountId; naming them is what lets a
    # deployment split compute spend from AI spend later.
    assert body["newapiAccountId"] == body["accountId"]
    assert body["ccproxyAccountId"] == body["accountId"]


async def test_an_ai_state_we_do_not_know_yet_reads_as_unknown():
    client = FakeMicroCloud()
    service = build_service(client)
    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    client.machines[machine.machine_id]["aiStatus"] = "some-future-state"
    await service.refresh(machine)
    assert machine.ai_status == AiStatus.unknown


async def test_an_unreachable_provider_does_not_claim_the_agent_is_ready():
    class Down(FakeMicroCloud):
        async def get_machine(self, machine_id):
            raise MicroCloudError("boom")

    client = Down()
    service = build_service(client)
    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")
    machine.ai_status = AiStatus.ready

    await service.refresh(machine)
    assert machine.ai_status == AiStatus.unknown


async def test_a_destroyed_machine_stops_occupying_the_projects_slot():
    """Delete-then-create must work.

    Destroying a machine leaves the row behind until a later read confirms
    MicroCloud has forgotten it. Counting those rows made the project's slots
    unreclaimable: the delete returned 200 and the next create was refused for a
    machine that no longer existed.
    """
    from app.core.config import settings

    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()

    made = []
    for _ in range(settings.microcloud_max_machines_per_project):
        made.append(await service.provision(project_id=project_id, requested_by="andy"))

    with pytest.raises(ValidationError):
        await service.provision(project_id=project_id, requested_by="andy")

    # Destroy one for real: MicroCloud forgets it, and the next read notices.
    await service.destroy(made[0])
    await service.list_for_project(project_id)

    replacement = await service.provision(project_id=project_id, requested_by="andy")
    assert replacement is not None


async def test_a_forgotten_machine_disappears_from_the_listing():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(project_id=project_id, requested_by="andy")

    client.machines.clear()  # MicroCloud no longer knows it
    remaining = await service.list_for_project(project_id)

    assert remaining == [], "a tombstone is not a machine anyone can use"
    assert machine not in await service._repo.list_for_project(project_id)


async def test_forgetting_an_enrolled_machine_removes_its_device():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(
        project_id=project_id, requested_by="andy", owner_user_id=42
    )
    machine.device_id = "cloud-device"
    service._devices.devices[machine.device_id] = SimpleNamespace(owner_user_id=42)

    client.machines.clear()
    await service.list_for_project(project_id)

    assert service._devices.deleted == ["cloud-device"]
    assert machine not in await service._repo.list_for_project(project_id)


async def test_forgetting_never_deletes_a_device_owned_by_somebody_else():
    client = FakeMicroCloud()
    service = build_service(client)
    project_id = uuid.uuid4()
    machine = await service.provision(
        project_id=project_id, requested_by="andy", owner_user_id=42
    )
    machine.device_id = "reassigned-device"
    service._devices.devices[machine.device_id] = SimpleNamespace(owner_user_id=99)

    client.machines.clear()
    await service.list_for_project(project_id)

    assert service._devices.deleted == []
    assert "reassigned-device" in service._devices.devices


# ---- built-in AI channel (→ccproxy, operator guidance) -----------------------


async def test_provision_switches_the_ai_channel_to_ccproxy():
    client = FakeMicroCloud()
    service = build_service(client)

    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    # newapi's default routes to a cheap non-Claude model; provision must not
    # leave a machine there.
    assert client.ai_switches == [(machine.machine_id, "ccproxy")]
    assert machine.ai_mode == "ccproxy"  # UPPERCASE reply normalized
    assert machine.ai_status == AiStatus.provisioning


async def test_provision_lands_even_when_the_switch_fails():
    client = FakeMicroCloud()

    async def boom(machine_id, mode):
        raise MicroCloudError("switch endpoint down")

    client.switch_ai = boom
    service = build_service(client)

    machine = await service.provision(project_id=uuid.uuid4(), requested_by="andy")

    # Best-effort: the machine still provisions; the sweep reconciles later.
    assert machine.ai_mode == "newapi"


async def test_sweep_reconciles_a_machine_left_on_newapi():
    client = FakeMicroCloud()
    repo = FakeRepo()
    service = build_service(client, repo=repo)
    repo.rows.append(
        SimpleNamespace(
            id=uuid.uuid4(),
            machine_id=463,
            hostname="m-1",
            project_id=uuid.uuid4(),
            status=MachineStatus.running,
            ai_status=AiStatus.ready,
            ai_mode="newapi",
            ip="10.0.0.5",
            device_id=None,
        )
    )

    switched = await service.reconcile_ai_mode()

    assert switched == 1
    assert client.ai_switches == [(463, "ccproxy")]
    row = repo.rows[0]
    assert row.ai_mode == "ccproxy"
    assert row.ai_status == AiStatus.provisioning


async def test_reconcile_is_off_without_a_desired_mode(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "microcloud_ai_mode", "")
    client = FakeMicroCloud()
    repo = FakeRepo()
    service = build_service(client, repo=repo)
    repo.rows.append(
        SimpleNamespace(
            id=uuid.uuid4(),
            machine_id=1,
            hostname="m-2",
            project_id=uuid.uuid4(),
            status=MachineStatus.running,
            ai_status=AiStatus.ready,
            ai_mode="newapi",
            ip=None,
            device_id=None,
        )
    )

    assert await service.reconcile_ai_mode() == 0
    assert client.ai_switches == []
