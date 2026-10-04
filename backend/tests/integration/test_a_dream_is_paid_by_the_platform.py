"""Consolidating a project's memory is the platform's own work, so the platform
pays for it (#2233): what a dream turn spends never reaches the project's
team, and never surfaces in the project's own spend for a later room turn to
be charged with."""

import uuid

import pytest
from sqlalchemy import select

from app.domain.agent import gateway as gw
from app.domain.agent.dream_usage import drain_dream_spend
from app.domain.memory import dream
from app.domain.project.repositories import ProjectRepository
from app.domain.usage.credits import CREDIT_USD
from app.domain.usage.models import ResourceUsage
from tests.integration.test_gateway_usage import FakeGateway, _mk_service

pytestmark = pytest.mark.anyio


class KeyedGateway(FakeGateway):
    """Serves each virtual key its own spend, so the test can tell whose
    spend landed where."""

    def __init__(self) -> None:
        super().__init__()
        self.platform_minted: list[uuid.UUID] = []
        self.by_key: dict[str, dict[str, tuple[int, int, float]]] = {}

    async def mint_project_key(self, project_id, *, platform: bool = False):
        if platform:
            self.platform_minted.append(project_id)
            return f"sk-platform-{len(self.platform_minted)}"
        return await super().mint_project_key(project_id)

    async def daily_spend_by_model(self, key, date):
        if date != gw.utc_today():
            return {}
        return {
            name: gw.ModelSpend(name, p, c, usd)
            for name, (p, c, usd) in self.by_key.get(key, {}).items()
        }


def _token(kwargs: tuple[dict, str]) -> str:
    return kwargs[0]["env"]["ANTHROPIC_AUTH_TOKEN"]


async def test_a_dream_runs_on_a_key_the_project_does_not_pay_for(
    business_db_factory, tmp_path
):
    fake = KeyedGateway()
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    room = _token(await svc._model_kwargs(pid, None, tid))
    dreaming = _token(await svc._model_kwargs(pid, None, tid, platform=True))
    again = _token(await svc._model_kwargs(pid, None, tid, platform=True))

    assert dreaming != room
    assert again == dreaming  # minted once, then reused
    assert fake.platform_minted == [pid]


async def test_what_a_dream_spends_is_the_platforms_and_no_team_pays(
    business_db_factory, tmp_path, monkeypatch
):
    fake = KeyedGateway()
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    room = _token(await svc._model_kwargs(pid, None, tid))
    dreaming = _token(await svc._model_kwargs(pid, None, tid, platform=True))
    fake.by_key[dreaming] = {"deepseek-flash": (9000, 900, 0.40)}
    fake.by_key[room] = {"deepseek-flash": (100, 10, 0.04)}
    run = uuid.uuid4()

    await drain_dream_spend(factory, fake, svc._gateway_lock, pid, tid, run)
    # A room turn's drain afterwards reads only the key the project pays for.
    await svc.charge_turn_spend(pid, tid, uuid.uuid4())

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ResourceUsage).where(ResourceUsage.project_id == pid)
                )
            ).scalars()
        )
        team_id = (await ProjectRepository(session).get(pid)).team_id
    dream_rows = [r for r in rows if r.turn_id == run]
    room_rows = [r for r in rows if r.turn_id != run]

    assert [(r.cost_usd, r.kind, r.team_id, r.credits) for r in dream_rows] == [
        (pytest.approx(0.40), dream.DREAM_KIND, None, 0.0)
    ]
    assert [(r.cost_usd, r.team_id) for r in room_rows] == [
        (pytest.approx(0.04), team_id)
    ]
    assert sum(r.credits for r in rows) == pytest.approx(0.04 / CREDIT_USD)
