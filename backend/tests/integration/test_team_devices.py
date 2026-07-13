"""Compute belongs to the team (execution-architecture v4).

A machine registered for a team (为团队注册设备) is usable by every project of that
team — without an explicit per-project assignment. This is the team-ownership layer:
bind a device to a team once, and the team's projects can run on it.
``SqlDeviceRepository.list_devices_by_project`` backs both the affinity routing and
the self-hosted pool's availability, so testing it here proves the behavior for both.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.device.models import DeviceRow
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import Team
from app.domain.user.models import User


def _now() -> datetime:
    return datetime.now(UTC)


def _project(client, name: str = "P") -> str:
    return client.post(
        "/api/projects", json={"name": name, "owner_handle": "andyl"}
    ).json()["data"]["id"]


def test_project_can_use_a_device_registered_for_its_team(client):
    team_pid = _project(client, "team-project")
    other_pid = _project(client, "other-project")

    async def seed() -> None:
        async with client.test_factory() as s:
            user = User(
                username="dave",
                email="dave@example.io",
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(user)
            await s.flush()
            team = Team(
                name="Team Cheese",
                intro="i",
                description="d",
                avatar_id=0,
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(team)
            await s.flush()
            s.add(
                DeviceRow(
                    device_id="devteam01",
                    name="Dave's box",
                    token="tok-devteam01",
                    owner_user_id=user.id,
                    created_at=_now(),
                )
            )
            await s.flush()
            # 为团队注册设备: bind the machine to the team (NOT to any project).
            await SqlDeviceRepository(s).assign_team("devteam01", team.id)
            # Link only the team-project to that team.
            proj = await ProjectRepository(s).get(uuid.UUID(team_pid))
            proj.team_id = team.id
            await s.commit()

    async def devices_for(pid: str) -> list[str]:
        async with client.test_factory() as s:
            found = await SqlDeviceRepository(s).list_devices_by_project(uuid.UUID(pid))
            return [d.device_id for d in found]

    asyncio.run(seed())
    # The team's project sees the team machine via the device_team binding — no
    # explicit device_project row needed.
    assert "devteam01" in asyncio.run(devices_for(team_pid))
    # A project NOT on that team does not — compute is scoped to the team context.
    assert "devteam01" not in asyncio.run(devices_for(other_pid))


def test_device_team_binding_is_idempotent_and_removable(client):
    async def run() -> tuple[list[int], list[int]]:
        async with client.test_factory() as s:
            user = User(
                username="erin", email="erin@x.io", created_at=_now(), updated_at=_now()
            )
            s.add(user)
            await s.flush()
            s.add(
                DeviceRow(
                    device_id="devteam02",
                    name="Erin's box",
                    token="tok-devteam02",
                    owner_user_id=user.id,
                    created_at=_now(),
                )
            )
            await s.flush()
            repo = SqlDeviceRepository(s)
            await repo.assign_team("devteam02", 7)
            await repo.assign_team("devteam02", 7)  # idempotent — no duplicate row
            await repo.assign_team("devteam02", 9)
            bound = sorted(await repo.list_team_ids("devteam02"))
            await repo.unassign_team("devteam02", 7)
            after = sorted(await repo.list_team_ids("devteam02"))
            await s.commit()
            return bound, after

    bound, after = asyncio.run(run())
    assert bound == [7, 9]
    assert after == [9]
