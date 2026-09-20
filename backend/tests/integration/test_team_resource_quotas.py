"""Team limits are shared across projects without sharing across customers."""

import asyncio
import uuid

import pytest

from app.core.errors import ValidationError
from app.domain.machine.limits import (
    get_machine_limit,
    reset_team_machine_limit,
    set_machine_limit,
)
from app.domain.machine.services import MachineService
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.team.services import team_service
from app.domain.usage.repositories import ComputeGrantRepository
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.unit.test_machine_service import FakeMicroCloud


async def seed_projects(factory):
    async with factory() as session:
        teams = []
        for handle in ("quota_owner", "quota_other"):
            user = await UserRepository(session).create_user(
                username=handle, email=f"{handle}@example.com"
            )
            teams.append(await team_service(session).ensure_personal_team(user.id))
        projects = [
            Project(name=name, team_id=teams[team].id)
            for name, team in [("A", 0), ("B", 0), ("C", 1)]
        ]
        session.add_all(projects)
        await session.commit()
        return [t.id for t in teams], [p.id for p in projects]


@pytest.mark.anyio
async def test_last_cloud_slot_is_shared_and_serialized_across_projects(db_factory):
    teams, projects = await seed_projects(db_factory)
    async with db_factory() as session:
        await set_machine_limit(session, 1, teams[0])
        await session.commit()
    provider = FakeMicroCloud()

    async def provision(project_id):
        async with db_factory() as session:
            service = MachineService(session)
            service._client = provider
            try:
                machine = await service.provision(
                    project_id=project_id, requested_by="owner"
                )
                await session.commit()
                return machine
            except ValidationError as error:
                return error

    results = await asyncio.gather(provision(projects[0]), provision(projects[1]))
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    assert len(provider.created) == 1
    assert "1 / 1" in str(next(r for r in results if isinstance(r, ValidationError)))
    assert not isinstance(await provision(projects[2]), ValidationError)
    assert len(provider.created) == 2
    async with db_factory() as session:
        assert len(await MachineService(session).quota_machines(teams[0])) == 1
        assert len(await MachineService(session).quota_machines(teams[1])) == 1


@pytest.mark.anyio
async def test_team_override_survives_default_change_and_can_inherit_again(db_factory):
    teams, _ = await seed_projects(db_factory)
    async with db_factory() as session:
        assert await get_machine_limit(session, teams[0]) == 50
        await set_machine_limit(session, 12, teams[0])
        await set_machine_limit(session, 75)
        await session.commit()
        assert await get_machine_limit(session, teams[0]) == 12
        assert await get_machine_limit(session, teams[1]) == 75
        await reset_team_machine_limit(session, teams[0])
        assert await get_machine_limit(session, teams[0]) == 75


@pytest.mark.anyio
async def test_shared_tokens_and_earmarked_credits_keep_their_boundaries(db_factory):
    teams, (a, b, c) = await seed_projects(db_factory)
    async with db_factory() as session:
        grants = ComputeGrantRepository(session)
        restricted = await grants.grant(project_id=a, source_task_id=7, credits_total=3)
        shared = await grants.grant_team(teams[0], 10)
        await grants.consume(a, 5)
        await session.refresh(restricted)
        await session.refresh(shared)
        assert restricted.credits_used == 3
        assert shared.credits_used == 2
        assert (await grants.summary(b))["credits_remaining"] == 8
        assert (await grants.summary(c))["unlimited"] is True
        await grants.consume(b, 8)
        assert (await grants.summary(a))["credits_remaining"] == 0
        assert (await grants.summary(b))["unlimited"] is False
        new_project = Project(name="D", team_id=teams[0])
        session.add(new_project)
        await session.flush()
        assert (await grants.summary(new_project.id))["credits_remaining"] == 0
        assert (await grants.summary(new_project.id))["unlimited"] is False


@pytest.mark.anyio
async def test_concurrent_project_settlements_do_not_lose_team_spend(db_factory):
    teams, (a, b, _) = await seed_projects(db_factory)
    async with db_factory() as session:
        grants = ComputeGrantRepository(session)
        await grants.grant_team(teams[0], 5)
        await grants.grant_team(teams[0], 10)
        await session.commit()

    async def spend(project_id):
        async with db_factory() as session:
            await ComputeGrantRepository(session).consume(project_id, 6)
            await session.commit()

    await asyncio.gather(spend(a), spend(b))
    async with db_factory() as session:
        summary = await ComputeGrantRepository(session).summary(a)
        assert summary["credits_used"] == 12
        assert summary["credits_remaining"] == 3
        assert [g.credits_used for g in summary["grants"]] == [5, 7]


@pytest.mark.anyio
async def test_legacy_personal_projects_use_the_same_team_without_being_reassigned(
    db_factory,
):
    teams, (a, _, _) = await seed_projects(db_factory)
    async with db_factory() as session:
        legacy = Project(name="Legacy", owner_handle="quota_owner", team_id=None)
        session.add(legacy)
        await session.flush()
        await ComputeGrantRepository(session).grant_team(teams[0], 10)
        await set_machine_limit(session, 1, teams[0])
        service = MachineService(session, FakeMicroCloud())
        await service.provision(project_id=legacy.id, requested_by="quota_owner")
        with pytest.raises(ValidationError, match="1 / 1"):
            await service.provision(project_id=a, requested_by="quota_owner")
        await ComputeGrantRepository(session).consume(legacy.id, 3)
        assert (await ComputeGrantRepository(session).summary(a))[
            "credits_remaining"
        ] == 7
        await session.refresh(legacy)
        assert legacy.team_id is None


@pytest.mark.anyio
async def test_migration_preserves_existing_balances_and_project_restrictions(
    db_factory,
):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/d8a6b5c4e731_machine_limit.py"
    )
    spec = importlib.util.spec_from_file_location("team_quota_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def verify(connection):
        # Rollback leaves the application's test tables intact.
        connection.execute(text("CREATE SCHEMA quota_migration_check"))
        connection.execute(text("SET LOCAL search_path TO quota_migration_check"))
        connection.execute(text("CREATE TABLE team (id bigint PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE projects (id uuid PRIMARY KEY, team_id bigint)")
        )
        connection.execute(
            text(
                "CREATE TABLE compute_grants (id uuid PRIMARY KEY, "
                "project_id uuid NOT NULL, credits_total float, credits_used float)"
            )
        )
        project_id, grant_id = uuid.uuid4(), uuid.uuid4()
        connection.execute(text("INSERT INTO team VALUES (1)"))
        connection.execute(
            text("INSERT INTO projects VALUES (:id, 1)"), {"id": project_id}
        )
        connection.execute(
            text("INSERT INTO compute_grants VALUES (:id, :project, 100, 35)"),
            {"id": grant_id, "project": project_id},
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            row = connection.execute(
                text(
                    "SELECT team_id, project_id, credits_total, credits_used "
                    "FROM compute_grants"
                )
            ).one()
            assert tuple(row) == (1, project_id, 100, 35)
            migration.downgrade()
            assert connection.execute(
                text("SELECT project_id, credits_used FROM compute_grants")
            ).one() == (project_id, 35)

    async with db_factory() as session:
        connection = await session.connection()
        await connection.run_sync(verify)
        await session.rollback()


def test_team_quota_view_is_private_and_preserves_project_usage(client):
    owner = seed_user(client, "quota_viewer")
    outsider = seed_user(client, "quota_outsider")
    headers = {"Authorization": f"Bearer {owner}"}
    project = client.post("/projects", json={"name": "A"}, headers=headers).json()[
        "data"
    ]
    project_id = uuid.UUID(project["id"])

    async def fund():
        async with client.test_factory() as session:
            repo = ComputeGrantRepository(session)
            team_id = await ProjectRepository(session).team_for_project(project_id)
            await repo.grant_team(team_id, 10)
            await session.commit()
            return team_id

    team_id = asyncio.run(fund())
    path = f"/teams/{team_id}/resource-quotas"
    assert client.get(path).status_code == 401
    assert (
        client.get(path, headers={"Authorization": f"Bearer {outsider}"}).status_code
        == 404
    )
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["machines"] == {"used": 0, "limit": 50}
    assert data["credits"]["credits_remaining"] == 10
    assert data["projects"][0]["total_tokens"] == 0
    assert client.get(f"/projects/{project_id}/credits").status_code == 401
    assert (
        client.get(
            f"/projects/{project_id}/credits",
            headers={"Authorization": f"Bearer {outsider}"},
        ).status_code
        == 404
    )
    assert (
        client.get(f"/projects/{project_id}/credits", headers=headers).json()["data"][
            "credits_remaining"
        ]
        == 10
    )
