"""Portable device inventory.

Archived-room cleanup behavior is covered in test_archive_lifecycle.py.
"""

import shlex
import subprocess
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.device_provider import list_device_storage
from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService

pytestmark = pytest.mark.anyio


async def _seed_device(
    session, device_id: str, *, project_id: uuid.UUID | None = None
) -> None:
    """An enrolled self-hosted machine, optionally one the project may run on."""
    owner = await IdentityService(session).ensure_agent_user()
    session.add(
        DeviceRow(
            device_id=device_id,
            name=device_id,
            token=f"tok-{device_id}",
            owner_user_id=owner.id,
            created_at=datetime.now(UTC),
        )
    )
    session.add(HostedDeviceRow(device_id=device_id, owner_user_id=owner.id))
    await session.flush()
    if project_id is not None:
        await sql_device_service(session).assign_to_project(
            device_id, project_id, actor_user_id=owner.id
        )


@pytest.mark.parametrize("roots", [(), ("home",), ("work",), ("home", "work")])
async def test_device_listing_uses_one_portable_shell_and_ignores_symlinks(
    tmp_path, roots
):
    project, place = str(uuid.uuid4()), str(uuid.uuid4())
    outside = tmp_path / "outside"
    (outside / place).mkdir(parents=True)
    for kind in roots:
        root = tmp_path / ".cheese" / kind
        (root / project / place).mkdir(parents=True)
        (root / str(uuid.uuid4())).symlink_to(outside, target_is_directory=True)
        (root / project / str(uuid.uuid4())).symlink_to(
            outside, target_is_directory=True
        )

    class ShellHub:
        calls = 0

        async def exec(self, device_id, command, *, timeout):
            self.calls += 1
            result = subprocess.run(
                [*command[:-1], f"HOME={shlex.quote(str(tmp_path))}; {command[-1]}"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit": result.returncode,
                "truncated": False,
            }

    hub = ShellHub()
    assert await list_device_storage("fixture", hub=hub) == [
        (kind, project, place) for kind in roots
    ]
    assert hub.calls == 1
