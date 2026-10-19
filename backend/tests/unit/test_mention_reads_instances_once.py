"""一条消息点了几个 AI 队友时，实例表只读一次。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.agent_instance.models import AgentInstance
from app.domain.delivery import agent as delivery_agent
from app.domain.delivery import mention
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle

pytestmark = pytest.mark.anyio


class _Session:
    """只回答实例表那一问，并数它被问了几次。"""

    def __init__(self, rows):
        self.rows = rows
        self.instance_reads = 0

    async def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Delivery:
            # The retry guard asks which seats this block already reached;
            # none, in these tests.
            return iter(())
        assert entity is AgentInstance
        self.instance_reads += 1
        return iter(self.rows)


def _rows(n):
    return [SimpleNamespace(id=uuid.uuid4(), is_active=True) for _ in range(n)]


async def _record(monkeypatch, session, named):
    recorded = []

    async def fake_named_seats(*_a, **_k):
        return list(named)

    async def fake_record_agent(_session, _event, *, instance_id, **_k):
        recorded.append(instance_id)

    monkeypatch.setattr(mention, "named_seats", fake_named_seats)
    monkeypatch.setattr(mention, "record_agent", fake_record_agent)
    await mention.record_mentions(
        session,
        project_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        block_id=uuid.uuid4(),
        author="someone",
        content="hi",
        by_agent=False,
        occurred_at=datetime.now(UTC),
    )
    return recorded


async def test_three_seats_read_the_instance_table_once(monkeypatch):
    rows = _rows(4)
    seats = [agent_instance_handle(r.id) for r in rows[:3]]
    session = _Session(rows)

    recorded = await _record(monkeypatch, session, seats)

    assert session.instance_reads == 1
    assert recorded == [r.id for r in rows[:3]]


async def test_each_seat_gets_the_same_instance_as_the_one_by_one_lookup():
    rows = _rows(4)
    seats = [agent_instance_handle(r.id) for r in rows[:3]] + ["cheese-000000000000"]
    project = uuid.uuid4()

    batch = await delivery_agent.instances_for_seats(_Session(rows), project, seats)
    for seat in seats:
        single = await delivery_agent.instance_for_seat(_Session(rows), project, seat)
        assert batch.get(seat) is single


async def test_a_seat_with_no_instance_is_skipped(monkeypatch):
    rows = _rows(2)
    seats = [agent_instance_handle(rows[0].id), "cheese-000000000000"]
    session = _Session(rows)

    recorded = await _record(monkeypatch, session, seats)

    assert recorded == [rows[0].id]
    assert session.instance_reads == 1
