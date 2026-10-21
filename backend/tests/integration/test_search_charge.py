"""A web search the platform runs costs credits, paid by whoever pays for the
work that asked, and is no model call.

DeepSeek bills $0.75 per 1,000 successful searches and a credit is a hundredth
of a dollar, so one search is 0.075 credits.
"""

from datetime import UTC, datetime
from math import isclose

import pytest

from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.usage.ledger import Ledger, payer_for_person, payer_for_project
from app.domain.usage.models import Plan
from app.domain.usage.repositories import UsageRepository
from app.domain.usage.search import charge_search
from tests.integration.conftest import registered

ONE_SEARCH = 0.75 / 1000 / 0.01


async def _team_with_project(session, handle: str):
    if await session.get(Plan, "bare") is None:
        session.add(Plan(key="bare", name="bare", windows=[], model_tiers=None))
        await session.flush()
    now = datetime.now(UTC)
    team = Team(
        name=handle,
        handle=handle,
        plan_key="bare",
        intro="",
        description="",
        avatar_id=0,
        created_at=now,
        updated_at=now,
    )
    session.add(team)
    await session.flush()
    project = Project(name="P", team_id=team.id)
    session.add(project)
    await session.flush()
    await Ledger(session).grant(team.id, 10)
    return team.id, project.id


@pytest.mark.anyio
async def test_a_room_agents_search_is_charged_to_its_project(db_factory):
    async with db_factory() as session:
        _, pid = await _team_with_project(session, "search-lab")
        payer = await payer_for_project(session, pid)
        ledger = Ledger(session)
        before = (await ledger.balance(payer)).credits_remaining

        await charge_search(session, payer)
        await charge_search(session, payer)
        await session.commit()

        after = (await ledger.balance(payer)).credits_remaining
        assert isclose(before - after, 2 * ONE_SEARCH)


@pytest.mark.anyio
async def test_a_persons_search_is_theirs_not_the_projects(db_factory):
    async with db_factory() as session:
        _, pid = await _team_with_project(session, "search-lab-2")
        person = await registered(session, "searcher")
        own = await payer_for_person(session, person)
        project = await payer_for_project(session, pid)
        ledger = Ledger(session)
        own_before = (await ledger.balance(own)).credits_remaining
        project_before = (await ledger.balance(project)).credits_remaining

        await charge_search(session, own, user_id=person)
        await session.commit()

        assert isclose(
            own_before - (await ledger.balance(own)).credits_remaining, ONE_SEARCH
        )
        assert (await ledger.balance(project)).credits_remaining == project_before


@pytest.mark.anyio
async def test_a_search_is_no_model_call(db_factory):
    async with db_factory() as session:
        _, pid = await _team_with_project(session, "search-lab-3")
        await charge_search(session, await payer_for_project(session, pid))
        await session.commit()

        spend = await UsageRepository(session).for_project(pid)
        assert spend["turns"] == 0
        assert spend["cost_usd"] == 0
        assert spend["total_tokens"] == 0
