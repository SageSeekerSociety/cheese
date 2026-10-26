"""The key a sandbox's model calls go out on.

The rules, as a person would state them:

- a sandbox's calls go out on a key of their own, capped per day on the
  gateway; the session's own calls keep the key they had;
- the key is minted once per conversation and budget holder, and a changed
  cap moves the key's daily budget rather than minting another;
- what a sandbox spent is charged when the conversation's turn is.
"""

import uuid

import pytest

from app.core.config import settings
from app.domain.agent import gateway as gw
from app.domain.usage.repositories import UsageRepository
from tests.integration.test_gateway_usage import FakeGateway, _mk_service

pytestmark = pytest.mark.anyio


class CappedGateway(FakeGateway):
    """A gateway that remembers the daily budget each key was minted with and
    spends only on the keys the test says spent."""

    def __init__(self) -> None:
        super().__init__()
        self.daily: dict[str, float | None] = {}
        self.spent: dict[str, dict[str, tuple[int, int, float]]] = {}

    async def mint_project_key(
        self, project_id, *, purpose=None, daily_budget_usd=None
    ):
        key = await super().mint_project_key(project_id, purpose=purpose)
        self.daily[key] = daily_budget_usd
        return key

    async def daily_spend_by_model(self, key, date):
        return {
            name: gw.ModelSpend(name, p, c, usd)
            for name, (p, c, usd) in self.spent.get(key, {}).items()
        }


async def test_a_sandboxs_calls_have_their_own_key_capped_per_day(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "machine_model_daily_budget_usd", 7.0)
    fake = CappedGateway()
    svc, _factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)

    machine = await svc.project_gateway_key(pid, machine_conversation_id=tid)
    again = await svc.project_gateway_key(pid, machine_conversation_id=tid)
    own = await svc.project_gateway_key(pid)

    assert machine and machine == again
    assert fake.daily[machine] == 7.0
    assert own and own != machine
    assert fake.daily[own] is None
    other = await svc.project_gateway_key(pid, machine_conversation_id=uuid.uuid4())
    assert other not in (machine, own)


async def test_a_changed_cap_moves_the_keys_budget_not_the_key(
    business_db_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "machine_model_daily_budget_usd", 7.0)
    fake = CappedGateway()
    svc, _factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    machine = await svc.project_gateway_key(pid, machine_conversation_id=tid)

    monkeypatch.setattr(settings, "machine_model_daily_budget_usd", 3.0)
    assert await svc.project_gateway_key(pid, machine_conversation_id=tid) == machine
    assert fake.budgets == [(machine, 3.0)]
    assert len(fake.minted) == 1


async def test_what_a_sandbox_spent_is_charged_with_the_turn(
    business_db_factory, tmp_path
):
    fake = CappedGateway()
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    machine = await svc.project_gateway_key(pid, machine_conversation_id=tid)
    assert machine
    fake.spent[machine] = {"deepseek-flash": (900, 100, 0.03)}

    usages = await svc.charge_turn_spend(pid, tid, uuid.uuid4())

    assert usages and [(u.model, u.input_tokens) for u in usages] == [
        ("deepseek-flash", 900)
    ]
    async with factory() as session:
        total = await UsageRepository(session).for_project(pid)
    assert total["cost_usd"] == pytest.approx(0.03)
    # Charged once: the next turn finds nothing new on the key.
    assert await svc.charge_turn_spend(pid, tid, uuid.uuid4()) is None


async def test_every_holders_sandbox_spend_is_charged(business_db_factory, tmp_path):
    fake = CappedGateway()
    svc, factory, pid, tid = await _mk_service(business_db_factory, tmp_path, fake)
    first = await svc.project_gateway_key(
        pid, credit_owner_handle="a", machine_conversation_id=tid
    )
    second = await svc.project_gateway_key(
        pid, credit_owner_handle="b", machine_conversation_id=tid
    )
    assert first and second and first != second
    fake.spent[first] = {"deepseek-flash": (10, 1, 0.01)}
    fake.spent[second] = {"glm-5.3-flash": (20, 2, 0.02)}

    usages = await svc.charge_turn_spend(pid, tid, uuid.uuid4())

    assert usages and sorted(u.model for u in usages) == [
        "deepseek-flash",
        "glm-5.3-flash",
    ]
    assert await svc.charge_turn_spend(pid, tid, uuid.uuid4()) is None
