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

from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Visibility
from app.domain.project.repositories import ProjectRepository
from app.domain.team.models import Team, TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
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
                    # This suite tests team/project routing, not the access axis;
                    # pin the honest personal-box value so it never rides on the
                    # column default.
                    visibility=Visibility.isolated,
                )
            )
            s.add(HostedDeviceRow(device_id="devteam01", owner_user_id=user.id))
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

    # The team's own compute view lists exactly its registered machines.
    async def team_devices(team_name: str) -> list[str]:
        async with client.test_factory() as s:
            from sqlalchemy import select

            from app.domain.team.models import Team

            tid = (
                await s.scalars(select(Team.id).where(Team.name == team_name))
            ).first()
            found = await SqlDeviceRepository(s).list_devices_by_team(int(tid))
            return [d.device_id for d in found]

    assert asyncio.run(team_devices("Team Cheese")) == ["devteam01"]


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
                    visibility=Visibility.isolated,
                )
            )
            s.add(HostedDeviceRow(device_id="devteam02", owner_user_id=user.id))
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


def test_personal_project_uses_owners_personal_team_devices(client):
    """个人 = 单人真团队 (v4): a project with NO team resolves compute through its
    owner's personal team — a machine registered there reaches every personal
    project of that user with zero per-project setup."""
    personal_pid = _project(client, "andyl-personal")

    async def seed() -> str:
        async with client.test_factory() as s:
            # `_project` creates projects with owner_handle="andyl"; handle ==
            # User.username, so this user is the owner of `personal_pid`.
            user = User(
                username="andyl",
                email="andyl@example.io",
                created_at=_now(),
                updated_at=_now(),
            )
            stranger = User(
                username="mallory",
                email="mallory@example.io",
                created_at=_now(),
                updated_at=_now(),
            )
            s.add_all([user, stranger])
            await s.flush()
            svc = TeamService(TeamRepository(session=s))
            mine = await svc.ensure_personal_team(user.id)
            again = await svc.ensure_personal_team(user.id)
            assert again.id == mine.id  # idempotent — one personal team per user
            theirs = await svc.ensure_personal_team(stranger.id)
            for did, owner in (("devper01", user), ("devper02", stranger)):
                s.add(
                    DeviceRow(
                        device_id=did,
                        name=f"{owner.username}'s box",
                        token=f"tok-{did}",
                        owner_user_id=owner.id,
                        created_at=_now(),
                        visibility=Visibility.isolated,
                    )
                )
                s.add(HostedDeviceRow(device_id=did, owner_user_id=owner.id))
            await s.flush()
            repo = SqlDeviceRepository(s)
            await repo.assign_team("devper01", mine.id)
            await repo.assign_team("devper02", theirs.id)
            await s.commit()
            return str(mine.id)

    async def devices_for(pid: str) -> list[str]:
        async with client.test_factory() as s:
            found = await SqlDeviceRepository(s).list_devices_by_project(uuid.UUID(pid))
            return [d.device_id for d in found]

    mine_id = int(asyncio.run(seed()))
    found = asyncio.run(devices_for(personal_pid))
    # The owner's personal-team machine routes to their team-less project…
    assert "devper01" in found
    # …but another user's personal machine never leaks in.
    assert "devper02" not in found

    # Once the project joins a shared team, the personal fallback is out of play:
    # only explicit assignments + that team's machines apply.
    async def move_to_shared_team() -> None:
        async with client.test_factory() as s:
            team = Team(
                name="Shared",
                intro="i",
                description="d",
                avatar_id=0,
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(team)
            await s.flush()
            proj = await ProjectRepository(s).get(uuid.UUID(personal_pid))
            proj.team_id = team.id
            await s.commit()

    asyncio.run(move_to_shared_team())
    assert "devper01" not in asyncio.run(devices_for(personal_pid))
    assert mine_id > 0  # sanity: the personal team really was provisioned


def test_ensure_personal_team_makes_a_real_single_member_team(client):
    """The personal team is a REAL team: one row flagged personal_owner_user_id,
    with the user as its OWNER member — not a synthetic no-team state."""

    async def run() -> tuple[bool, int, int]:
        async with client.test_factory() as s:
            user = User(
                username="frank",
                email="frank@example.io",
                created_at=_now(),
                updated_at=_now(),
            )
            s.add(user)
            await s.flush()
            repo = TeamRepository(session=s)
            team = await TeamService(repo).ensure_personal_team(user.id)
            members = await repo.list_members_of_team(team.id)
            await s.commit()
            return (
                team.personal_owner_user_id == user.id,
                len(members),
                members[0].role,
            )

    flagged, member_count, role = asyncio.run(run())
    assert flagged
    assert member_count == 1
    assert role == TeamMemberRole.OWNER
