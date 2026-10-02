"""Who pays for a model call, in which order its credit packs are spent, and
when a call is refused (#2397).

Every pack belongs to a team; a person's own credits are packs on their
personal team. These tests drive the ledger against a real database the way
its callers do: name the payer, ask the balance, charge what a call cost.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.team.services import team_service
from app.domain.usage.ledger import (
    Ledger,
    payer_for_person,
    payer_for_project,
)
from app.domain.usage.models import GrantSource
from tests.integration.conftest import registered


async def _shared_team(session, handle: str) -> int:
    now = datetime.now(UTC)
    team = Team(
        name=handle,
        handle=handle,
        intro="",
        description="",
        avatar_id=0,
        created_at=now,
        updated_at=now,
    )
    session.add(team)
    await session.flush()
    return team.id


async def _project(session, team_id: int, name: str = "P") -> uuid.UUID:
    project = Project(name=name, team_id=team_id)
    session.add(project)
    await session.flush()
    return project.id


async def _personal_team(session, user_id: int) -> int:
    return (await team_service(session).ensure_personal_team(user_id)).id


async def _used(session, pack) -> float:
    await session.refresh(pack)
    return pack.credits_used


@pytest.mark.anyio
async def test_a_call_is_refused_once_every_pack_it_may_use_is_spent(db_factory):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        pid = await _project(session, team)
        ledger = Ledger(session)
        await ledger.grant_earmark(project_id=pid, source_task_id=1, credits_total=2)
        await ledger.grant(team, 3)
        payer = await payer_for_project(session, pid)

        await ledger.charge(payer, 4.5)
        assert not (await ledger.balance(payer)).exhausted
        await ledger.charge(payer, 0.5)

        assert (await ledger.balance(payer)).exhausted


@pytest.mark.anyio
async def test_earmark_then_team_plan_then_bought_credits(db_factory):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        pid = await _project(session, team)
        ledger = Ledger(session)
        # Issued in the reverse of the order they are spent in.
        bought = await ledger.grant(team, 10, source=GrantSource.PURCHASE)
        plan = await ledger.grant(
            team,
            10,
            source=GrantSource.PLAN_PERIOD,
            expires_at=datetime.now(UTC) + timedelta(days=10),
        )
        earmark = await ledger.grant_earmark(
            project_id=pid, source_task_id=1, credits_total=10
        )
        payer = await payer_for_project(session, pid)

        await ledger.charge(payer, 15)
        assert await _used(session, earmark) == 10
        assert await _used(session, plan) == 5
        assert await _used(session, bought) == 0

        await ledger.charge(payer, 10)
        assert await _used(session, plan) == 10
        assert await _used(session, bought) == 5


@pytest.mark.anyio
async def test_a_personal_projects_calls_are_not_charged_to_the_monthly_pack(
    db_factory,
):
    """Until plans land (#2397), a person's monthly pack pays only for what
    they ask outside a project; their own projects run as before."""
    async with db_factory() as session:
        owner = await registered(session, "owner")
        team = await _personal_team(session, owner)
        pid = await _project(session, team)
        ledger = Ledger(session)
        own = await payer_for_person(session, owner)
        [monthly] = (await ledger.balance(own)).packs
        await ledger.charge(own, monthly.credits_total)  # the month is spent

        project = await payer_for_project(session, pid)
        assert not (await ledger.balance(project)).exhausted
        await ledger.charge(project, 3)
        assert await _used(session, monthly) == monthly.credits_total

        # A pack the team holds otherwise is still the project's to spend.
        bought = await ledger.grant(team, 10, source=GrantSource.PURCHASE)
        await ledger.charge(project, 3)
        assert await _used(session, bought) == 3


@pytest.mark.anyio
async def test_of_bought_and_granted_credits_what_lapses_first_is_spent_first(
    db_factory,
):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        pid = await _project(session, team)
        ledger = Ledger(session)
        forever = await ledger.grant(team, 10, source=GrantSource.PURCHASE)
        soon = await ledger.grant(
            team, 10, expires_at=datetime.now(UTC) + timedelta(days=3)
        )
        later = await ledger.grant(
            team, 10, expires_at=datetime.now(UTC) + timedelta(days=30)
        )

        await ledger.charge(await payer_for_project(session, pid), 15)

        assert await _used(session, soon) == 10
        assert await _used(session, later) == 5
        assert await _used(session, forever) == 0


@pytest.mark.anyio
async def test_another_members_call_in_a_personal_project_charges_its_owners_team(
    db_factory,
):
    """Where the call happens decides who pays: a personal project pays from
    its owner's personal team, not the caller's."""
    async with db_factory() as session:
        owner = await registered(session, "owner")
        guest = await registered(session, "guest")
        owners = await _personal_team(session, owner)
        guests = await _personal_team(session, guest)
        pid = await _project(session, owners)
        ledger = Ledger(session)
        mine = await ledger.grant(owners, 10, source=GrantSource.PURCHASE)
        theirs = await ledger.grant(guests, 10, source=GrantSource.PURCHASE)

        await ledger.charge(await payer_for_project(session, pid), 4)

        assert await _used(session, mine) == 4
        assert await _used(session, theirs) == 0


@pytest.mark.anyio
async def test_the_last_call_may_overdraw_and_is_recorded_truthfully(db_factory):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        pid = await _project(session, team)
        ledger = Ledger(session)
        await ledger.grant(team, 1)
        payer = await payer_for_project(session, pid)

        assert await ledger.charge(payer, 4) == 4

        balance = await ledger.balance(payer)
        assert balance.credits_used == 4
        assert balance.credits_remaining == -3
        assert balance.exhausted


@pytest.mark.anyio
async def test_a_lapsed_pack_cannot_be_spent_but_late_spend_still_lands(db_factory):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        pid = await _project(session, team)
        ledger = Ledger(session)
        lapsed = await ledger.grant(
            team, 10, expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        payer = await payer_for_project(session, pid)

        balance = await ledger.balance(payer)
        assert balance.credits_total == 0

        # Spend the gateway reports after the pack lapsed is still booked.
        await ledger.charge(payer, 2)
        assert await _used(session, lapsed) == 2


@pytest.mark.anyio
async def test_a_payer_with_no_pack_runs_only_when_the_deployment_says_so(
    db_factory, monkeypatch
):
    async with db_factory() as session:
        team = await _shared_team(session, "lab")
        payer = await payer_for_project(session, await _project(session, team))
        ledger = Ledger(session)

        monkeypatch.setattr(settings, "credits_unlimited", True)
        assert not (await ledger.balance(payer)).exhausted

        monkeypatch.setattr(settings, "credits_unlimited", False)
        assert (await ledger.balance(payer)).exhausted


@pytest.mark.anyio
async def test_a_month_issues_one_plan_pack_however_many_first_requests(db_factory):
    async with db_factory() as session:
        me = await registered(session, "asker")
        await _personal_team(session, me)
        await session.commit()

    async def ask() -> None:
        async with db_factory() as session:
            await Ledger(session).balance(await payer_for_person(session, me))
            await session.commit()

    await asyncio.gather(*(ask() for _ in range(5)))

    async with db_factory() as session:
        balance = await Ledger(session).balance(await payer_for_person(session, me))
        assert len(balance.packs) == 1
        assert balance.credits_total == settings.personal_credits_monthly
        assert balance.resets_at is not None


@pytest.mark.anyio
async def test_a_batch_of_projects_reads_what_each_reads_alone(db_factory):
    from app.domain.usage.services import UsageService

    async with db_factory() as session:
        owner = await registered(session, "owner")
        team = await _shared_team(session, "lab")
        projects = [
            await _project(session, team, "A"),
            await _project(session, team, "B"),
            await _project(session, await _personal_team(session, owner), "C"),
        ]
        ledger = Ledger(session)
        await ledger.grant(team, 10)
        await ledger.grant_earmark(
            project_id=projects[0], source_task_id=1, credits_total=5
        )
        await ledger.charge(await payer_for_person(session, owner), 1)
        rows = [await session.get(Project, pid) for pid in projects]

        usage = UsageService(session)
        batch = await usage.project_credits_batch(rows)
        for pid in projects:
            alone = await usage.project_credits(pid)
            assert {k: v for k, v in batch[pid].items() if k != "grants"} == {
                k: v for k, v in alone.items() if k != "grants"
            }


@pytest.mark.anyio
async def test_the_migration_moves_each_persons_credits_onto_their_personal_team(
    db_factory,
):
    """A person's monthly grant keeps its balance, now on their personal team,
    which is created for anyone who had credits but no personal team yet."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/f2a9c4e7b318_every_credit_pack_belongs_to_a_team.py"
    )
    spec = importlib.util.spec_from_file_location("credit_pack_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def verify(connection):
        # Rollback leaves the application's test tables intact.
        for statement in (
            "CREATE SCHEMA credit_pack_migration_check",
            "SET LOCAL search_path TO credit_pack_migration_check",
            "CREATE SEQUENCE team_seq",
            "CREATE SEQUENCE team_user_relation_seq",
            'CREATE TABLE "user" (id int PRIMARY KEY)',
            "CREATE TABLE team (id bigint PRIMARY KEY, name text, handle text,"
            " intro text, description text, avatar_id int,"
            " personal_owner_user_id int, created_at timestamptz,"
            " updated_at timestamptz, deleted_at timestamptz)",
            "CREATE TABLE team_user_relation (id bigint PRIMARY KEY,"
            " team_id bigint, user_id int, role smallint, created_at timestamptz,"
            " updated_at timestamptz, deleted_at timestamptz)",
            "CREATE TABLE projects (id uuid PRIMARY KEY, team_id bigint)",
            "CREATE TABLE compute_grants (id uuid PRIMARY KEY, team_id bigint,"
            " project_id uuid, source_task_id bigint, user_id int, month date,"
            " credits_total float, credits_used float)",
            "CREATE UNIQUE INDEX uq_compute_grants_user_month ON compute_grants"
            " (user_id, month) WHERE user_id IS NOT NULL",
            'INSERT INTO "user" VALUES (1), (2)',
            # Person 1 has a personal team already; person 2 does not.
            "INSERT INTO team (id, name, personal_owner_user_id)"
            " VALUES (nextval('team_seq'), '个人', 1)",
            "INSERT INTO team (id, name, handle)"
            " VALUES (nextval('team_seq'), 'T', 't')",
        ):
            connection.execute(text(statement))
        project = uuid.uuid4()
        connection.execute(text("INSERT INTO projects VALUES (:p, 2)"), {"p": project})
        for user_id, used in ((1, 50.0), (2, 7.0)):
            connection.execute(
                text(
                    "INSERT INTO compute_grants (id, user_id, month, credits_total,"
                    " credits_used) VALUES (:id, :u, '2026-10-01', 200, :used)"
                ),
                {"id": uuid.uuid4(), "u": user_id, "used": used},
            )
        connection.execute(
            text(
                "INSERT INTO compute_grants (id, project_id, credits_total,"
                " credits_used) VALUES (:id, :p, 30, 3)"
            ),
            {"id": uuid.uuid4(), "p": project},
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(
            text(
                "SELECT t.personal_owner_user_id, g.source, g.credits_total,"
                " g.credits_used, g.expires_at FROM compute_grants g"
                " JOIN team t ON t.id = g.team_id ORDER BY g.credits_total, 1"
            )
        ).all()
        lapses = datetime(2026, 11, 1, tzinfo=UTC) - timedelta(hours=8)
        assert [tuple(r) for r in rows] == [
            (None, "task_earmark", 30, 3, None),
            (1, "plan_period", 200, 50, lapses),
            (2, "plan_period", 200, 7, lapses),
        ]
        owners = connection.execute(
            text(
                "SELECT r.user_id, r.role FROM team t JOIN team_user_relation r"
                " ON r.team_id = t.id WHERE t.personal_owner_user_id = 2"
            )
        ).all()
        assert [tuple(r) for r in owners] == [(2, 0)]

    async with db_factory() as session:
        connection = await session.connection()
        await connection.run_sync(verify)
        await session.rollback()
