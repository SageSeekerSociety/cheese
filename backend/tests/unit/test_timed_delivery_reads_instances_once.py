"""定时投递一批旧行补身份时，实例表每个项目只读一次，各行只认本项目的实例。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent_instance.models import AgentInstance
from app.domain.delivery import timer
from app.domain.identity.handles import agent_instance_handle

pytestmark = pytest.mark.anyio


class _Session:
    """第一问返回到期行，其余只答实例表，并数实例表被问了几次。"""

    def __init__(self, due, instances):
        self.due, self.instances = due, instances
        self.instance_reads = 0
        self.first = True

    async def scalars(self, statement):
        if self.first:
            self.first = False
            return iter(self.due)
        assert statement.column_descriptions[0]["entity"] is AgentInstance
        self.instance_reads += 1
        wanted = set(statement.compile().params.values())
        return iter(i for i in self.instances if i.project_id in wanted)

    async def commit(self):
        pass


def _legacy_row(project, seat):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project,
        recipient_handle=seat,
        agent_instance_id=None,
        receiver_id=None,
        event_id=None,
        materialized_at=None,
    )


async def _run(monkeypatch, session):
    handed = []

    async def hand(_session, row):
        handed.append(row)

    async def nothing(*_a, **_k):
        return 0

    monkeypatch.setattr(timer, "_hand_to_agent", hand)
    monkeypatch.setattr(timer, "give_up_stale", nothing)
    monkeypatch.setattr(timer, "dispatch_pending", nothing)

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return False

    await timer.deliver_due(_Factory(), chat=None, runner=None)
    return handed


async def test_many_legacy_rows_read_the_instance_table_once(monkeypatch):
    project = uuid.uuid4()
    instances = [SimpleNamespace(id=uuid.uuid4(), project_id=project) for _ in range(3)]
    rows = [_legacy_row(project, agent_instance_handle(i.id)) for i in instances]
    rows.append(_legacy_row(project, agent_instance_handle(instances[0].id)))
    session = _Session(rows, instances)

    handed = await _run(monkeypatch, session)

    assert session.instance_reads == 1
    assert [r.agent_instance_id for r in handed] == [
        instances[0].id,
        instances[1].id,
        instances[2].id,
        instances[0].id,
    ]


async def test_a_row_whose_seat_has_no_instance_is_left_pending(monkeypatch):
    project = uuid.uuid4()
    row = _legacy_row(project, "cheese-000000000000")
    session = _Session([row], [])

    handed = await _run(monkeypatch, session)

    assert handed == []
    assert row.materialized_at is None


async def test_each_project_is_read_once_and_rows_match_their_own_project(monkeypatch):
    a, b = uuid.uuid4(), uuid.uuid4()
    in_a = [SimpleNamespace(id=uuid.uuid4(), project_id=a) for _ in range(2)]
    in_b = [SimpleNamespace(id=uuid.uuid4(), project_id=b)]
    rows = [
        _legacy_row(a, agent_instance_handle(in_a[0].id)),
        _legacy_row(b, agent_instance_handle(in_b[0].id)),
        _legacy_row(a, agent_instance_handle(in_a[1].id)),
        _legacy_row(b, agent_instance_handle(in_a[0].id)),
    ]
    session = _Session(rows, in_a + in_b)

    handed = await _run(monkeypatch, session)

    assert session.instance_reads == 2
    assert [r.agent_instance_id for r in handed] == [
        in_a[0].id,
        in_b[0].id,
        in_a[1].id,
    ]
