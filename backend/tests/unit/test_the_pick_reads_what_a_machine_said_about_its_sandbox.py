"""「系统挑一台」 reads what each machine said about giving a session a sandbox.

A room it places is isolated, so a machine that said it cannot make a sandbox
is never the one picked. What a machine said is settled when it connects, by
the very check its install runs; the pick does not ask it again.
"""

import json
import subprocess
import sys
import uuid

from app.domain.agent.harness.claude_code import executor_launch
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply

OWNER = 1


async def _enrol(service: DeviceService, project_id: uuid.UUID, name: str) -> str:
    device = await service.approve(
        await service.start(name), owner_user_id=OWNER, supply=Supply.self_hosted
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


async def test_a_machine_that_said_it_cannot_isolate_is_not_picked():
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    refusing = await _enrol(service, project, "old box")
    able = await _enrol(service, project, "new box")
    online = {refusing, able}.__contains__
    assert (await service.first_healthy_device(project, online)).device_id == refusing

    await service.record_sandbox_check(refusing, isolates=False, reason="no userns")

    assert (await service.first_healthy_device(project, online)).device_id == able


async def test_with_only_such_machines_nothing_is_picked():
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    only = await _enrol(service, project, "old box")
    await service.record_sandbox_check(only, isolates=False, reason="no userns")

    assert await service.first_healthy_device(project, {only}.__contains__) is None


async def test_a_machine_that_can_again_is_picked_again():
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    box = await _enrol(service, project, "box")
    await service.record_sandbox_check(box, isolates=False, reason="no userns")
    await service.record_sandbox_check(box, isolates=True, reason=None)

    assert (await service.first_healthy_device(project, {box}.__contains__)) is not None


def test_the_check_a_machine_runs_answers_in_one_line():
    """The program a connecting machine runs: the install's own check, ending
    in one line that says whether it can and, if not, why."""
    done = subprocess.run(
        [sys.executable, "-"],
        input=executor_launch.sandbox_check_script(),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    answer = json.loads(done.stdout.strip().splitlines()[-1])
    assert isinstance(answer["isolates"], bool)
    assert answer["isolates"] or answer["reason"]
