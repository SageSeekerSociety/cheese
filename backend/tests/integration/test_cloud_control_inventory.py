"""Cloud control forwards every live host of the pool, and nothing else.

The forward is what lets a cloud host's connector reach the backend at all
(its API and control channel are loopback ports on the box), so a host the
inventory leaves out runs no tool. The inventory is the program
``deploy/cloud-control.py`` sends into the backend container; this runs that
very program against the test database.
"""

import json
import os
import runpy
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply
from app.domain.identity.services import IdentityService
from app.domain.machine.models import AiStatus, CloudHost, MachineStatus, WarmMachine
from tests.conftest import TEST_DATABASE_URL

CONTROL = Path(__file__).resolve().parents[3] / "deploy/cloud-control.py"


def _inventory() -> list[dict]:
    program = runpy.run_path(str(CONTROL))["INVENTORY"]
    result = subprocess.run(
        [sys.executable, "-"],
        input=program,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_inventory_lists_the_pools_live_hosts_and_ready_warm_machines(client):
    async def seed():
        async with client.test_factory() as db:
            identity = IdentityService(db)
            owner = await identity.ensure_agent_user(handle="cheese-host-pool")
            now = datetime.now(UTC)

            def device(device_id, private=True):
                db.add(
                    DeviceRow(
                        device_id=device_id,
                        name=device_id,
                        token=f"token-{device_id}",
                        owner_user_id=owner.id,
                        supply=Supply.cloud,
                        cloud_control_private=private,
                        created_at=now,
                    )
                )

            def host(device_id, ip, machine_id, **fields):
                db.add(
                    CloudHost(
                        id=uuid.uuid4(),
                        machine_id=machine_id,
                        customer_id=7,
                        account_id=9,
                        offering_id=1,
                        hostname=f"host-{machine_id}",
                        login_user="cheese",
                        cores=2,
                        memory_mb=4096,
                        disk_gb=20,
                        status=fields.pop("status", MachineStatus.running),
                        ai_status=AiStatus.disabled,
                        ip=ip,
                        device_id=device_id,
                        **fields,
                    )
                )

            for name in ("live", "released", "broken", "public", "warm"):
                device(name, private=name != "public")
            await db.flush()
            host("live", "192.0.2.11", 11)
            host("released", "192.0.2.13", 13, released_at=now)
            host("broken", "192.0.2.14", 14, status=MachineStatus.error)
            # Reached over its own public route, not through cloud control.
            host("public", "192.0.2.15", 15)
            db.add(
                WarmMachine(
                    state="ready",
                    machine_id=16,
                    device_id="warm",
                    ip="192.0.2.16",
                    enrolled_at=now,
                    create_request={"user": "cheese"},
                )
            )
            await db.commit()

    client.portal.call(seed)

    listed = {row["device_id"]: row for row in _inventory()}

    assert set(listed) == {"live", "warm"}
    assert listed["live"] == {
        "machine_id": 11,
        "device_id": "live",
        "ip": "192.0.2.11",
        "login_user": "cheese",
    }
