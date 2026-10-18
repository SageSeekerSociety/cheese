"""Task receipts keep the execution's payer despite overlap and late settlement."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent import gateway as gw
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.queries import turn_starts
from app.domain.room_task.repositories import TaskRepository
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.subscription_ingest import ingest_once
from tests.integration.conftest import registered
from tests.integration.test_gateway_usage import FakeGateway, _mk_service
from tests.integration.test_subscription_ingest import OPUS, _price, _row, _rows, _seed


async def _open(session, pid, room, owner, *, task=None, started=None, agent=None):
    if task is None:
        task = await TaskRepository(session).add(
            project_id=pid,
            room_id=room,
            title=owner,
            owner_handle=owner,
            created_by=owner,
        )
    task.owner_handle = owner
    await session.flush()
    work = uuid.uuid4()
    await AgentTurnRepository(session).open(
        turn_id=work,
        conversation_id=task.id,
        continuation_id=work,
        author=owner,
        content="go",
        is_resume=False,
        resendable=True,
        started_at=started or datetime.now(UTC),
        agent_handle=agent,
    )
    return task, work


@pytest.mark.anyio
@pytest.mark.parametrize("elapsed", [timedelta(seconds=40), timedelta(hours=7)])
async def test_subscription_receipt_names_its_execution_during_overlap(
    business_db_factory, tmp_path, monkeypatch, elapsed
):
    _price(monkeypatch, {"claude-opus-5": OPUS})
    pid, room = await _seed(business_db_factory)
    started = datetime.now(UTC) - elapsed
    async with business_db_factory() as session:
        await registered(session, "other-human")
        task, first = await _open(session, pid, room, "u", started=started, agent="a")
        _, second = await _open(
            session,
            pid,
            room,
            "other-human",
            task=task,
            started=started + timedelta(seconds=30),
            agent="b",
        )
        task_id = task.id
        await session.commit()
    log = tmp_path / "overlap.jsonl"
    log.write_text(
        _row(pid, task_id, inp=11, ts=(started + elapsed).timestamp(), work_id=first)
        + _row(pid, task_id, inp=21, ts=(started + elapsed).timestamp(), work_id=second)
    )
    assert await ingest_once(business_db_factory, log, turn_starts=turn_starts) == {
        "landed": 2,
        "skipped": 0,
    }
    rows = await _rows(business_db_factory, pid)
    assert [(r.turn_id, r.credit_owner_handle) for r in rows] == [
        (first, "u"),
        (second, "other-human"),
    ]


class TaskGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.spend = {}

    async def daily_spend_by_model(self, key, date):
        return {"m": gw.ModelSpend("m", 10, 2, self.spend.get(key, 0))}


@pytest.mark.anyio
async def test_gateway_tasks_and_public_calls_have_separate_accounts(
    business_db_factory, tmp_path
):
    fake = TaskGateway()
    svc, factory, pid, room = await _mk_service(business_db_factory, tmp_path, fake)
    async with factory() as session:
        await registered(session, "other-human")
        task_a, first = await _open(session, pid, room, "u")
        task_b, second = await _open(session, pid, room, "other-human")
        a, b = task_a.id, task_b.id
        await session.commit()
    public_key = await svc.project_gateway_key(pid)
    first_key = await svc.project_gateway_key(pid, task_id=a, credit_owner_handle="u")
    second_key = await svc.project_gateway_key(
        pid, task_id=b, credit_owner_handle="other-human"
    )
    assert len({public_key, first_key, second_key}) == 3
    fake.spend = {public_key: 0.04, first_key: 0.02, second_key: 0.03}
    await svc.charge_turn_spend(pid, a, first)
    await svc.charge_turn_spend(pid, b, second)
    await svc.charge_turn_spend(pid, room, uuid.uuid4())
    async with factory() as session:
        report = await CreditBudgets(session).report(pid)
    assert report["members"]["u"]["credits_used"] == pytest.approx(2)
    assert report["members"]["other-human"]["credits_used"] == pytest.approx(3)
    assert report["public_credits"] == pytest.approx(4)
