"""Nearly spent credits are said once, to the people the cap will stop.

Three caps stop different sets of people, so each is read on its own terms and
told to its own readers: a team's plan credits to the whole team, a project's
budget to the people in it, a member's task cap to that member.

What makes it once is the delivery ledger's dedup key, which is built from what
was crossed: a second sweep over an unchanged cap writes nothing, and a raised
limit crossed again is a second warning. These tests therefore drive the real
ledger rather than a counting stub, and read the rows it wrote.
"""

import uuid

import pytest
from sqlalchemy import select

from app.domain.addressing import Event, Hand, address
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent, NotificationType
from app.domain.notification.models import Notification
from app.domain.usage import alerts
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.ledger import Ledger, payer_for_project
from app.domain.usage.models import ResourceUsage
from tests.integration.conftest import project_service, registered


async def _notify(session, event_id, payload, handles) -> None:
    """The wiring's deliverer, exactly as ``app.periodic_jobs`` hands it in."""
    for handle in handles:
        await deliver(
            session,
            DeliveryEvent(
                id=event_id,
                type=NotificationType.CREDIT_WARNING,
                payload=payload,
                occurred_at=alerts.datetime.now(alerts.UTC),
            ),
            address(Event(audience=(handle,)), Hand.participant),
        )


async def _said(factory) -> list[Notification]:
    """Every credit warning written, oldest first."""
    async with factory() as session:
        rows = await session.scalars(
            select(Notification)
            .where(Notification.type == NotificationType.CREDIT_WARNING.value)
            .order_by(Notification.id)
        )
        return list(rows)


async def _seed(factory) -> uuid.UUID:
    async with factory() as session:
        await registered(session, "u")
        await registered(session, "mate")
        project = await project_service(session).create(name="P", owner_handle="u")
        pid = project.id
        await session.commit()
    return pid


async def _spend(factory, pid, credits: float) -> None:
    """Spend credits inside the project, the way a call there does: one usage
    row carrying the project, and the charge it is settled with."""
    async with factory() as session:
        payer = await payer_for_project(session, pid)
        await Ledger(session).record(
            payer,
            credits=credits,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=credits / 100,
            route="gateway",
            kind="chat",
        )
        await session.commit()


async def _sweep(factory, *, team=("u", "mate"), project=("u", "mate")) -> int:
    async def team_people(session, team_id):
        return list(team)

    async def project_people(session, project_id):
        return list(project)

    return await alerts.sweep(
        factory,
        team_people=team_people,
        project_people=project_people,
        notify=_notify,
    )


@pytest.mark.anyio
async def test_a_crossing_is_said_once(business_db_factory):
    pid = await _seed(business_db_factory)

    # A Free team issues 500 a month, so 400 is the 80% crossing.
    await _spend(business_db_factory, pid, 400)
    assert await _sweep(business_db_factory) == 1

    rows = await _said(business_db_factory)
    # 一封信一个人：投递账本写下来的行按 receiver_id 认人，名册上的名字那一列
    # 留给项目收件箱的另一条路。
    assert len(rows) == 2
    assert "还剩 100" in rows[0].metadata_payload["content"]

    # The same cap, read again: nothing written.
    await _sweep(business_db_factory)
    assert len(await _said(business_db_factory)) == 2


@pytest.mark.anyio
async def test_below_the_threshold_says_nothing(business_db_factory):
    pid = await _seed(business_db_factory)

    await _spend(business_db_factory, pid, 300)
    assert await _sweep(business_db_factory) == 0
    assert await _said(business_db_factory) == []


@pytest.mark.anyio
async def test_spent_credits_are_the_ledgers_own_refusal(business_db_factory):
    """At 100% the ledger's refusal speaks; the warning does not repeat it."""
    pid = await _seed(business_db_factory)

    await _spend(business_db_factory, pid, 500)
    assert await _sweep(business_db_factory) == 0


@pytest.mark.anyio
async def test_a_raised_budget_is_news_again(business_db_factory):
    pid = await _seed(business_db_factory)

    async with business_db_factory() as session:
        await CreditBudgets(session).save(
            pid, credits_limit=100.0, set_project_limit=True, member_limits={}
        )
        await session.commit()
    await _spend(business_db_factory, pid, 80)
    assert await _sweep(business_db_factory) == 1

    async with business_db_factory() as session:
        await CreditBudgets(session).save(
            pid, credits_limit=1000.0, set_project_limit=True, member_limits={}
        )
        await session.commit()
    await _sweep(business_db_factory)
    assert len(await _said(business_db_factory)) == 2

    await _spend(business_db_factory, pid, 720)
    assert await _sweep(business_db_factory) >= 1
    contents = [row.metadata_payload["content"] for row in await _said(business_db_factory)]
    assert any("不会自动恢复" in text for text in contents)


@pytest.mark.anyio
async def test_a_member_cap_reaches_only_that_member(business_db_factory):
    pid = await _seed(business_db_factory)

    async with business_db_factory() as session:
        await CreditBudgets(session).save(
            pid,
            credits_limit=None,
            set_project_limit=True,
            member_limits={"u": 100.0},
        )
        payer = await payer_for_project(session, pid)
        await Ledger(session).record(
            payer,
            credits=85.0,
            user_id=None,
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.85,
            route="gateway",
            kind="chat",
            attribute_to_work=False,
        )
        # `record` names the person from the execution's owner, which only a
        # task has; a member cap is read off the column, so the row is stamped
        # here the way `CreditBudgets.execution` would have.
        await session.execute(
            ResourceUsage.__table__.update()
            .where(ResourceUsage.project_id == pid)
            .values(credit_owner_handle="u")
        )
        await session.commit()

    assert await _sweep(business_db_factory) == 1
    rows = await _said(business_db_factory)
    assert len(rows) == 1
    assert rows[0].metadata_payload["layer"] == "member"
