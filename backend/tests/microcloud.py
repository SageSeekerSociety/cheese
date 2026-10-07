"""A fake MicroCloud for tests: the remote control plane, in memory."""

from app.domain.machine.microcloud import MicroCloudError

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
        self.topups: list[tuple[int, float]] = []
        self.machines: dict[int, dict] = {}
        self._next_id = 100
        self.customers: dict[str, dict] = {}
        self.accounts: dict[tuple[int, str], dict] = {}
        self.claims = []
        self.wakes: list[tuple[str, int]] = []

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

    #: Set to make every machine call fail as when MicroCloud is unreachable.
    down = False

    async def get_machine(self, machine_id):
        if self.down:
            raise MicroCloudError("MicroCloud unreachable: connection refused")
        return self.machines.get(machine_id)

    # --- waking a parked machine -------------------------------------------

    #: What a resume or start leaves the machine in: ``resuming``/``starting``
    #: as MicroCloud answers an accepted one, or e.g. ``suspended`` for one
    #: that failed back.
    wakes_to: str | None = None

    def _woken(self, machine_id, kind, moving):
        if self.down:
            raise MicroCloudError("MicroCloud unreachable: connection refused")
        self.wakes.append((kind, machine_id))
        machine = self.machines[machine_id]
        machine["status"] = self.wakes_to or moving
        return dict(machine)

    async def resume_machine(self, machine_id):
        return self._woken(machine_id, "resume", "resuming")

    async def start_machine(self, machine_id):
        return self._woken(machine_id, "start", "starting")

    async def find_machine(self, customer_id, hostname):
        for machine in self.machines.values():
            if machine.get("hostname") == hostname:
                return machine
        return None

    async def delete_machine(self, machine_id):
        self.deleted.append(machine_id)
        self.machines.pop(machine_id, None)

    # --- warm machines ---------------------------------------------------

    claims: list[tuple[int, dict]]
    fail_claim = False

    async def claim_warm_machine(self, machine_id, body):
        self.claims.append((machine_id, body))
        if self.fail_claim:
            raise MicroCloudError("response lost")
        return {"id": machine_id, "status": "running", "aiStatus": "disabled"}
