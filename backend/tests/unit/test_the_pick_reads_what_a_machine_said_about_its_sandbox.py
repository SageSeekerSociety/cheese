"""Placing a sandbox on a person's computers reads what each machine said
about giving a session a sandbox.

A machine that said it cannot make one is never where a sandbox goes. What a
machine said is settled when it connects, by the very check its install runs;
the placement does not ask it again.
"""

import json
import subprocess
import sys
import uuid
from types import SimpleNamespace

from app.domain.agent.environment import devices_of
from app.domain.agent.harness.claude_code import executor_launch
from app.domain.device import sandbox
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply
from app.domain.machine import environments
from app.domain.machine.environments import device_for_spec

OWNER = 1


async def _enrol(service: DeviceService, project_id: uuid.UUID, name: str) -> str:
    device = await service.approve(
        await service.start(name), owner_user_id=OWNER, supply=Supply.self_hosted
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


async def _place(service, project, held=None):
    """Where a sandbox of ``OWNER``'s computers would go now."""
    device = await device_for_spec(
        _Session(service),
        spec=devices_of(OWNER),
        project=SimpleNamespace(id=project, team_id=None),
        task=None,
        held=held,
        is_online=lambda _device: True,
    )
    return device.device_id if device else None


class _Session:
    """What ``device_for_spec`` reads through, with the device service the
    test holds in place of the SQL one."""

    def __init__(self, service):
        self.service = service


async def test_a_machine_that_said_it_cannot_isolate_is_not_picked(monkeypatch):
    service = _patched(monkeypatch)
    project = uuid.uuid4()
    refusing = await _enrol(service, project, "old box")
    able = await _enrol(service, project, "new box")
    assert await _place(service, project, held=refusing) == refusing

    await service.record_sandbox_check(
        refusing, isolates=False, reason='{"key": "sandboxAppArmor", "params": {}}'
    )

    assert await _place(service, project, held=refusing) == able


async def test_with_only_such_machines_nothing_is_picked(monkeypatch):
    service = _patched(monkeypatch)
    project = uuid.uuid4()
    only = await _enrol(service, project, "old box")
    await service.record_sandbox_check(
        only, isolates=False, reason='{"key": "sandboxAppArmor", "params": {}}'
    )

    assert await _place(service, project) is None


async def test_a_machine_that_can_again_is_picked_again(monkeypatch):
    service = _patched(monkeypatch)
    project = uuid.uuid4()
    box = await _enrol(service, project, "box")
    await service.record_sandbox_check(
        box, isolates=False, reason='{"key": "sandboxAppArmor", "params": {}}'
    )
    await service.record_sandbox_check(box, isolates=True, reason=None)

    assert await _place(service, project) == box


def _patched(monkeypatch) -> DeviceService:
    service = DeviceService(InMemoryDeviceRepository())
    monkeypatch.setattr(environments, "sql_device_service", lambda db: db.service)
    return service


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
    assert answer["isolates"] or answer["said"]["key"].startswith("sandbox")


async def test_an_owner_reads_the_sentence_a_machine_said_and_nothing_else():
    """A reason that is not a sentence (English written before reasons were
    keyed, or by the release still serving during a deploy) reads as none,
    rather than as text the page cannot render."""
    service = DeviceService(InMemoryDeviceRepository())
    box = await _enrol(service, uuid.uuid4(), "box")

    await service.record_sandbox_check(
        box,
        isolates=False,
        reason=sandbox.kept({"key": "sandboxAppArmor", "params": {}}),
    )
    assert sandbox.status(await service.sandbox_check(box))["reason"] == {
        "key": "sandboxAppArmor",
        "params": {},
    }

    await service.record_sandbox_check(
        box, isolates=False, reason="bubblewrap is missing"
    )
    assert sandbox.status(await service.sandbox_check(box))["reason"] is None
