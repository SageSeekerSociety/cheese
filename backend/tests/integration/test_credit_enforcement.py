"""Plans enforced (#2397): a monthly plan issues every team its pack, a
windowed plan lets a team spend up to each window's cap and then only what it
bought, Reserve refuses nothing, a plan limits models by tier, and a project
key's gateway budget follows what the project may still spend."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app.api import deps as session_turn_deps
from app.domain.policy import gate
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.team.services import team_service
from app.domain.usage.ledger import (
    Ledger,
    month_end,
    month_of,
    payer_for_person,
    payer_for_project,
    team_terms,
)
from app.domain.usage.models import (
    ComputeGrant,
    GrantSource,
    Plan,
    PlanWindowUse,
    ResourceUsage,
)
from app.domain.usage.services import UsageService
from tests.integration.conftest import registered


async def _team(session, handle: str, plan: str = "free") -> int:
    now = datetime.now(UTC)
    team = Team(
        name=handle,
        handle=handle,
        plan_key=plan,
        intro="",
        description="",
        avatar_id=0,
        created_at=now,
        updated_at=now,
    )
    session.add(team)
    await session.flush()
    return team.id


async def _project(session, team_id: int) -> uuid.UUID:
    project = Project(name="P", team_id=team_id)
    session.add(project)
    await session.flush()
    return project.id


async def _free(session, **fields) -> None:
    await session.execute(update(Plan).where(Plan.key == "free").values(**fields))


async def _spend(ledger: Ledger, payer, credits: float):
    """A call that cost ``credits``."""
    await ledger.record(
        payer,
        credits=credits,
        model="m",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        route="gateway",
    )


@pytest.mark.anyio
async def test_a_shared_teams_project_is_issued_its_month_and_refused_once_spent(
    db_factory,
):
    async with db_factory() as session:
        await _free(session, credits_per_period=10)
        payer = await payer_for_project(
            session, await _project(session, await _team(session, "lab"))
        )
        ledger = Ledger(session)

        assert await ledger.admit(payer) is None
        await _spend(ledger, payer, 10)
        packs = (await ledger.balance(payer)).packs
        assert [(p.source, p.credits_total) for p in packs] == [("plan_period", 10)]

        refused = await ledger.admit(payer)
        assert refused is not None
        assert refused.reopens_at == packs[0].expires_at
        local = refused.reopens_at.astimezone(ZoneInfo("Asia/Shanghai"))
        assert f"{local.month}月{local.day}日" in refused.message


@pytest.mark.anyio
async def test_reserve_refuses_nothing_issues_nothing_and_still_records_usage(
    db_factory,
):
    async with db_factory() as session:
        team = await _team(session, "us", plan="reserve")
        payer = await payer_for_project(session, await _project(session, team))
        ledger = Ledger(session)

        await _spend(ledger, payer, 10_000)

        assert await ledger.admit(payer) is None
        assert (await ledger.balance(payer)).packs == ()
        recorded = await session.scalar(
            select(ResourceUsage.credits).where(ResourceUsage.team_id == team)
        )
        assert recorded == 10_000


async def _windowed(session, *windows: dict) -> None:
    """Put Free on time windows instead of a monthly pack."""
    await _free(session, credits_per_period=None, windows=list(windows))


async def _later(session, team_id: int, **elapsed) -> None:
    """Let time pass for a team's windows: every round started that much
    earlier."""
    await session.execute(
        update(PlanWindowUse)
        .where(PlanWindowUse.team_id == team_id)
        .values(started_at=PlanWindowUse.started_at - timedelta(**elapsed))
    )


@pytest.mark.anyio
async def test_a_windowed_plan_spends_no_pack_and_refuses_once_a_window_is_full(
    db_factory,
):
    async with db_factory() as session:
        await _windowed(session, {"hours": 5, "credits": 10})
        payer = await payer_for_project(
            session, await _project(session, await _team(session, "lab"))
        )
        ledger = Ledger(session)

        await _spend(ledger, payer, 6)
        assert await ledger.admit(payer) is None
        await _spend(ledger, payer, 4)

        assert (await ledger.balance(payer)).packs == ()
        refused = await ledger.admit(payer)
        assert refused is not None
        assert "5 小时" in refused.message


@pytest.mark.anyio
async def test_an_hours_window_resets_that_long_after_its_first_call(db_factory):
    async with db_factory() as session:
        await _windowed(session, {"hours": 5, "credits": 10})
        team = await _team(session, "lab")
        payer = await payer_for_project(session, await _project(session, team))
        ledger = Ledger(session)

        await _spend(ledger, payer, 4)
        await _later(session, team, hours=4)
        await _spend(ledger, payer, 6)

        # Full an hour before the round's five hours are up, not five hours
        # after its last call.
        refused = await ledger.admit(payer)
        assert refused is not None and refused.reopens_at is not None
        left = refused.reopens_at - datetime.now(UTC)
        assert timedelta(minutes=59) < left <= timedelta(hours=1)

        await _later(session, team, hours=1)
        assert await ledger.admit(payer) is None
        await _spend(ledger, payer, 3)
        [window] = (await ledger.balance(payer)).windows
        assert window.credits_used == 3


@pytest.mark.anyio
async def test_a_month_window_resets_on_the_first_of_the_month(db_factory):
    async with db_factory() as session:
        await _windowed(session, {"calendar": "month", "credits": 10})
        team = await _team(session, "lab")
        payer = await payer_for_project(session, await _project(session, team))
        ledger = Ledger(session)

        await _spend(ledger, payer, 10)
        refused = await ledger.admit(payer)
        assert refused is not None
        assert refused.reopens_at == month_end(month_of(datetime.now(UTC)))
        assert "本月的用量已达上限" in refused.message

        # Spent last month, the window is empty this month.
        await _later(session, team, days=31)
        assert await ledger.admit(payer) is None


@pytest.mark.anyio
async def test_a_week_window_resets_on_monday(db_factory):
    async with db_factory() as session:
        await _windowed(session, {"calendar": "week", "credits": 10})
        team = await _team(session, "lab")
        payer = await payer_for_project(session, await _project(session, team))
        ledger = Ledger(session)

        await _spend(ledger, payer, 10)
        refused = await ledger.admit(payer)
        assert refused is not None and refused.reopens_at is not None
        monday = refused.reopens_at.astimezone(ZoneInfo("Asia/Shanghai"))
        assert (monday.weekday(), monday.hour, monday.minute) == (0, 0, 0)
        assert timedelta(0) < refused.reopens_at - datetime.now(UTC) <= timedelta(7)

        await _later(session, team, days=7)
        assert await ledger.admit(payer) is None


@pytest.mark.anyio
async def test_an_earmark_and_bought_credits_never_fill_a_window(db_factory):
    """The earmark goes first; once a window is full, bought credits carry the
    team until they run out."""
    async with db_factory() as session:
        await _windowed(session, {"hours": 5, "credits": 10})
        team = await _team(session, "lab")
        pid = await _project(session, team)
        payer = await payer_for_project(session, pid)
        ledger = Ledger(session)
        earmark = await ledger.grant_earmark(
            project_id=pid, source_task_id=None, credits_total=5
        )
        bought = await ledger.grant(team, 20, source=GrantSource.PURCHASE)

        await _spend(ledger, payer, 5)
        await _spend(ledger, payer, 10)
        await session.refresh(earmark)
        await session.refresh(bought)
        assert earmark.credits_used == 5
        assert bought.credits_used == 0
        [window] = (await ledger.balance(payer)).windows
        assert window.credits_used == 10

        assert await ledger.admit(payer) is None
        await _spend(ledger, payer, 20)
        await session.refresh(bought)
        assert bought.credits_used == 20
        [window] = (await ledger.balance(payer)).windows
        assert window.credits_used == 10
        assert await ledger.admit(payer) is not None


@pytest.mark.anyio
async def test_a_plan_that_turns_windowed_leaves_this_months_pack_unspent(
    db_factory,
):
    async with db_factory() as session:
        await _free(session, credits_per_period=100)
        team = await _team(session, "lab")
        payer = await payer_for_project(session, await _project(session, team))
        await _spend(Ledger(session), payer, 10)

        await _windowed(session, {"hours": 5, "credits": 50})
        payer = await payer_for_project(session, payer.project_id)
        await _spend(Ledger(session), payer, 5)

        pack = await session.scalar(
            select(ComputeGrant).where(ComputeGrant.team_id == team)
        )
        assert pack is not None and pack.credits_used == 10


@pytest.mark.anyio
async def test_a_windowed_plans_gateway_budget_is_what_its_fullest_window_allows(
    db_factory, monkeypatch
):
    async with db_factory() as session:
        await _windowed(
            session, {"hours": 5, "credits": 10}, {"calendar": "week", "credits": 30}
        )
        pid = await _project(session, await _team(session, "lab"))
        await _spend(Ledger(session), await payer_for_project(session, pid), 4)

        credits = await UsageService(session).project_credits(pid)
        assert credits["credits_remaining"] == 6
        assert credits["gateway_budget_usd"] == pytest.approx(0.06)


@pytest.mark.anyio
async def test_a_free_team_cannot_use_a_model_above_its_plan_whatever_the_project(
    db_factory,
):
    """Over the plan is refused outright: no proposal can lift a plan."""
    async with db_factory() as session:
        free = await team_terms(session, await _team(session, "lab"))
        reserve = await team_terms(session, await _team(session, "us", "reserve"))
    premium = gate.Call(
        resource=gate.Resource.model,
        subject="opus",
        label="Claude Opus",
        tier="premium",
        approver="owner",
    )
    proposing = {"allowed_tiers": ["included"], "over_tier": "propose"}
    widened = {"allowed_tiers": ["included", "premium"]}

    for project_settings in (None, proposing, widened):
        policy = gate.policy_of(project_settings, free.model_tiers)
        with pytest.raises(gate.OverPlan):
            gate.check(premium, policy, "agent")

    allowed = gate.check(premium, gate.policy_of(None, reserve.model_tiers), "agent")
    assert isinstance(allowed, gate.Allowed)


@pytest.mark.anyio
async def test_a_project_narrows_a_plan_but_a_plan_never_limits_machines(db_factory):
    async with db_factory() as session:
        reserve = await team_terms(session, await _team(session, "us", "reserve"))
        free = await team_terms(session, await _team(session, "lab"))
    included = gate.Call(
        resource=gate.Resource.model,
        subject="glm",
        label="GLM",
        tier="included",
        approver="owner",
    )
    narrowed = gate.policy_of({"allowed_tiers": ["premium"]}, reserve.model_tiers)
    with pytest.raises(gate.OverTier):
        gate.check(included, narrowed, "agent")

    cloud = gate.Call(
        resource=gate.Resource.machine,
        subject="cloud",
        label="Cloud",
        tier="premium",
        approver="owner",
    )
    assert isinstance(
        gate.check(cloud, gate.policy_of(None, free.model_tiers), "agent"),
        gate.Allowed,
    )


@pytest.mark.anyio
async def test_the_gateway_budget_narrows_as_packs_lapse_and_clears_on_reserve(
    business_db_factory, tmp_path, monkeypatch
):
    from app.domain.agent.chat import ChatService
    from app.domain.project.services import ProjectService
    from tests.conftest import stub_compute
    from tests.integration.test_gateway_usage import FakeGateway

    fake = FakeGateway()
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=business_db_factory,
        compute=stub_compute(),
        base_system_prompt="",
        workspace_root=str(tmp_path / "ws"),
        gateway=fake,  # type: ignore[arg-type]
    )
    async with business_db_factory() as session:
        await _free(session, credits_per_period=100)
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        pack = await Ledger(session).grant(
            project.team_id, 50, expires_at=datetime.now(UTC) + timedelta(days=1)
        )
        pid, team, pack_id = project.id, project.team_id, pack.id
        await session.commit()

    key = await svc.project_gateway_key(pid)
    assert fake.budgets == [(key, 1.5)]

    async with business_db_factory() as session:
        payer = await payer_for_project(session, pid)
        await Ledger(session).record(
            payer,
            credits=20,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.2,
            route="gateway",
        )
        await session.execute(
            update(ComputeGrant)
            .where(ComputeGrant.id == pack_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    await svc.project_gateway_key(pid)
    # Spent 0.20 on the key, and 80 of the month's 100 credits are left.
    assert fake.budgets[-1] == (key, 1.0)

    async with business_db_factory() as session:
        found = await team_service(session).get_team(team)
        assert found is not None
        found.plan_key = "reserve"
        await session.commit()

    await svc.project_gateway_key(pid)
    assert fake.budgets[-1] == (key, None)


@pytest.mark.anyio
async def test_a_persons_questions_are_refused_once_the_month_is_spent(db_factory):
    async with db_factory() as session:
        await _free(session, credits_per_period=3)
        me = await registered(session, "asker")
        payer = await payer_for_person(session, me)
        ledger = Ledger(session)

        await _spend(ledger, payer, 3)

        refused = await ledger.admit(payer)
        assert refused is not None
        assert "本月额度已用完" in refused.message


@pytest.mark.anyio
async def test_a_turn_with_no_usage_report_still_names_the_team_that_pays(
    business_db_factory, tmp_path
):
    """Work whose tokens are unknown is still a row in its team's books."""
    from app.domain.project.services import ProjectService
    from tests.conftest import finish_turn
    from tests.integration.test_gateway_usage import QuietScreen, _mk_service

    svc, factory, pid, tid = await _mk_service(
        business_db_factory, tmp_path, None, screen=QuietScreen()
    )
    async for _ in svc.converse(
        topic_id=tid, author="u", content="做点事", summon=True
    ):
        pass
    await finish_turn(svc, tid)

    async with factory() as session:
        team = (await ProjectService(session).get_or_404(pid)).team_id
        rows = list(
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            ).scalars()
        )
    assert rows and all(r.kind.endswith(":unmetered") for r in rows)
    assert {r.team_id for r in rows} == {team}
