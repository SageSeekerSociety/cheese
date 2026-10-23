"""Quiet-task sweep batches room lookups after the quiet-window check.

Tasks outside the `[QUIET_NOTICE_AFTER, STALLED_AFTER)` window never touch the
`topics` table, and tasks inside the window fetch their rooms in one
`TopicService.get_many` call while still skipping archived (or missing) rooms.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from app.domain.room_task.models import TaskStatus
from app.domain.task_quiet import sweep
from app.domain.topic.models import Topic, TopicStatus

_NOW = datetime(2026, 10, 18, 9, 0, 0, tzinfo=UTC)
_PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _CountingSession:
    """Counts `Topic` reads across both `session.get` and `session.scalars`."""

    def __init__(self, rooms: list[Any]) -> None:
        self._rooms = {r.id: r for r in rooms}
        self.room_queries = 0

    async def get(self, model: Any, ident: uuid.UUID) -> Any:
        assert model is Topic
        self.room_queries += 1
        return self._rooms.get(ident)

    async def scalars(self, stmt: Any) -> list[Any]:
        self.room_queries += 1
        wanted = set(stmt.whereclause.right.value)
        return [r for tid, r in self._rooms.items() if tid in wanted]

    async def commit(self) -> None:
        pass


def _room(*, status: TopicStatus = TopicStatus.active, title: str = "room") -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=_PROJECT_ID,
        title=title,
        status=status,
    )


def _task(room_id: uuid.UUID, *, created_at: datetime, title: str = "task") -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=_PROJECT_ID,
        room_id=room_id,
        title=title,
        owner_handle="alice",
        status=TaskStatus.open,
        progress_state=None,
        progress_set_by=None,
        progress_run_harness=None,
        progress_run_epoch=None,
        progress_run_seq=None,
        accepted_at=None,
        conclusion=None,
        closing_since=None,
        created_at=created_at,
    )


def _wire_fakes(
    monkeypatch: Any,
    tasks: list[Any],
    *,
    last_said: dict[uuid.UUID, datetime] | None = None,
) -> list[tuple[Any, Any]]:
    delivered: list[tuple[Any, Any]] = []

    class FakeTaskService:
        def __init__(self, _session: Any) -> None:
            pass

        async def list_open(self) -> list[Any]:
            return list(tasks)

        async def last_block_at_for_tasks(
            self, _ids: list[uuid.UUID]
        ) -> dict[uuid.UUID, datetime]:
            return dict(last_said or {})

    async def fake_cards(_session: Any, _ids: list[uuid.UUID]) -> dict[uuid.UUID, Any]:
        return {}

    async def fake_asked(_session: Any, _ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return set()

    async def fake_runs(_session: Any, _tasks: Any) -> dict[uuid.UUID, Any]:
        return {}

    async def fake_deliver(_session: Any, event: Any, to: Any) -> None:
        delivered.append((event, to))

    monkeypatch.setattr(sweep, "TaskService", FakeTaskService)
    monkeypatch.setattr(sweep, "latest_cards_by_task", fake_cards)
    monkeypatch.setattr(sweep, "awaiting_an_answer", fake_asked)
    monkeypatch.setattr(sweep, "teammate_runs", fake_runs)
    monkeypatch.setattr(sweep, "deliver", fake_deliver)
    return delivered


async def test_task_outside_quiet_window_does_not_query_rooms(monkeypatch: Any) -> None:
    room = _room(status=TopicStatus.active, title="active-room")
    fresh_task = _task(room.id, created_at=_NOW - timedelta(days=1), title="fresh")
    delivered = _wire_fakes(monkeypatch, [fresh_task])
    session = _CountingSession([room])

    @asynccontextmanager
    async def sessions():
        yield cast(Any, session)

    reminded = await sweep.remind_quiet_tasks(sessions, now=_NOW)

    assert reminded == 0
    assert delivered == []
    assert session.room_queries == 0


async def test_three_quiet_tasks_query_rooms_once_and_deliver_only_to_unarchived(
    monkeypatch: Any,
) -> None:
    archived_a = _room(status=TopicStatus.archived, title="archived-a")
    archived_b = _room(status=TopicStatus.archived, title="archived-b")
    active = _room(status=TopicStatus.active, title="active-room")
    quiet_at = _NOW - timedelta(days=4)
    tasks = [
        _task(archived_a.id, created_at=quiet_at, title="t-archived-a"),
        _task(archived_b.id, created_at=quiet_at, title="t-archived-b"),
        _task(active.id, created_at=quiet_at, title="t-active"),
    ]
    delivered = _wire_fakes(monkeypatch, tasks)
    session = _CountingSession([archived_a, archived_b, active])

    @asynccontextmanager
    async def sessions():
        yield cast(Any, session)

    reminded = await sweep.remind_quiet_tasks(sessions, now=_NOW)

    assert session.room_queries == 1
    assert reminded == 1
    assert [event.payload["taskId"] for event, _ in delivered] == [str(tasks[2].id)]
    assert delivered[0][0].payload["topicId"] == str(active.id)
    assert delivered[0][0].payload["topicTitle"] == "active-room"
