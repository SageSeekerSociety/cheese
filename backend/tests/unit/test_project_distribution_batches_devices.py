"""``project_distribution`` asks for its machines' names in one batch.

The page lists a project's self-hosted machines with how many agents each one
holds. It used to call ``get_device`` once per machine; the names now come from
one ``get_devices`` call, whatever the number of machines. This pins the batch
call itself, so a later edit that reintroduces the per-machine loop fails here
before it costs a round trip per machine again.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.domain.agent_session.models import AgentEnvironment, AgentSession
from app.domain.machine import session_reports
from app.domain.topic.models import Topic, TopicStatus

PROJECT = uuid.uuid4()
ROOM = uuid.uuid4()


class _FakeDb:
    """Only ``execute`` is used: it answers the placement query with the rows
    the test seeded."""

    def __init__(self, rows) -> None:
        self._rows = rows

    async def execute(self, _statement):
        return self._rows


class _FakeDeviceService:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def get_devices(self, device_ids):
        self.calls.append(list(device_ids))
        return {
            "device-1": SimpleNamespace(name="alpha"),
            "device-2": SimpleNamespace(name="beta"),
        }


def _placed(handle: str, device_id: str):
    return (
        AgentEnvironment(
            conversation_id=ROOM,
            agent_handle=handle,
            environment={"kind": "self_hosted", "device_id": device_id},
        ),
        AgentSession(conversation_id=ROOM, agent_handle=handle),
        Topic(
            id=ROOM,
            project_id=PROJECT,
            title="a room",
            status=TopicStatus.active,
        ),
    )


async def test_the_distribution_reads_every_machine_in_one_call(monkeypatch) -> None:
    devices = _FakeDeviceService()
    monkeypatch.setattr(
        session_reports, "sql_device_service", lambda _db: devices
    )
    db = _FakeDb([_placed("one", "device-1"), _placed("two", "device-2")])

    distribution = await session_reports.project_distribution(db, PROJECT)

    assert devices.calls == [["device-1", "device-2"]]
    assert distribution["devices"] == [
        {"device_id": "device-1", "name": "alpha", "agents": 1},
        {"device_id": "device-2", "name": "beta", "agents": 1},
    ]
