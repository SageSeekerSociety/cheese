"""Compute belongs to the team (execution-architecture v4).

A device enrolled by a team member is usable by every project of that team —
without an explicit per-project assignment. This is the team-ownership layer:
enroll a machine once for the team, and the team's projects can run on it.
``SqlDeviceRepository.list_devices_by_project`` backs both the affinity routing
and the self-hosted pool's availability, so testing it here proves the behavior
end to end for both.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.device.models import DeviceRow
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
from app.domain.user.models import User


def _now() -> datetime:
    return datetime.now(UTC)


def _project(client, name: str = "P") -> str:
    return client.post(
        "/api/projects", json={"name": name, "owner_handle": "andyl"}
    ).json()["data"]["id"]


def test_project_can_use_its_teams_device_without_explicit_assignment(client):
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
                TeamUserRelation(
                    team_id=team.id,
                    user_id=user.id,
                    role=TeamMemberRole.MEMBER,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            # Dave enrolled this machine (as himself, a team member) — NOT assigned
            # to any project explicitly.
            s.add(
                DeviceRow(
                    device_id="devteam01",
                    name="Dave's box",
                    token="tok-devteam01",
                    owner_user_id=user.id,
                    created_at=_now(),
                )
            )
            # Link only the team-project to Dave's team.
            proj = await ProjectRepository(s).get(uuid.UUID(team_pid))
            proj.team_id = team.id
            await s.commit()

    async def devices_for(pid: str) -> list[str]:
        async with client.test_factory() as s:
            found = await SqlDeviceRepository(s).list_devices_by_project(uuid.UUID(pid))
            return [d.device_id for d in found]

    asyncio.run(seed())
    # The team's project sees Dave's machine via team membership — no explicit
    # device_project row needed.
    assert "devteam01" in asyncio.run(devices_for(team_pid))
    # A project NOT on that team does not — compute is scoped to the team context.
    assert "devteam01" not in asyncio.run(devices_for(other_pid))
