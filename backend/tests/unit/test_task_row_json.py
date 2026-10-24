"""`task_row_json` is byte-identical to the per-row Pydantic path, and cheaper."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone

from app.domain.room_task.adapters import task_row_json
from app.domain.room_task.models import Task, TaskStatus, TaskTitleSource
from app.domain.room_task.schemas import TaskOut

PROJECT_ID = uuid.UUID(int=2)
ROOM_ID = uuid.UUID(int=1)
T0 = datetime(2026, 10, 17, 12, 0, 0, 123456, tzinfo=UTC)


def _tasks(count: int) -> list[Task]:
    return [
        Task(
            id=uuid.UUID(int=i + 10),
            project_id=PROJECT_ID,
            room_id=ROOM_ID,
            number=i if i % 9 else None,
            title=f"任务 {i}",
            title_source=TaskTitleSource.human if i % 4 else TaskTitleSource.auto,
            status=TaskStatus.open if i % 2 else TaskStatus.closed,
            owner_handle=f"u{i % 7}" if i % 5 else None,
            reviewer_handle="r" if i % 3 else None,
            contributor_handles=[f"h{i}", "x"] if i % 6 == 0 else [],
            created_by="c",
            created_by_assistant=i % 3 == 1,
            branch_name=f"task/{i}" if i % 4 == 0 else None,
            base_branch="main",
            base_task_id=uuid.UUID(int=i) if i % 10 == 0 else None,
            historical_delivery_id=uuid.UUID(int=i) if i % 11 == 0 else None,
            pr_number=i if i % 4 == 0 else None,
            document_id=uuid.UUID(int=i + 5) if i % 7 == 0 else None,
            accepted_by="a" if i % 13 == 0 else None,
            accepted_at=T0 if i % 13 == 0 else None,
            closed_at=T0.astimezone(timezone(timedelta(hours=8)))
            if i % 5 == 0
            else None,
            source_block_id=uuid.UUID(int=i + 7) if i % 3 == 0 else None,
            upgraded_from_block_id=uuid.UUID(int=i + 9) if i % 17 == 0 else None,
            conclusion=f"done {i}" if i % 8 == 0 else None,
            created_at=T0,
            updated_at=T0.replace(tzinfo=None) if i % 19 == 0 else T0,
        )
        for i in range(count)
    ]


def _bytes(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False).encode()


def _old(tasks):
    return [TaskOut.model_validate(t).model_dump(mode="json") for t in tasks]


def _new(tasks):
    return [task_row_json(t) for t in tasks]


def test_1000_rows_are_byte_identical_to_task_out() -> None:
    tasks = _tasks(1000)
    assert _bytes(_new(tasks)) == _bytes(_old(tasks))


def test_rows_are_built_without_pydantic_validation(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("per-row model_validate is back")

    monkeypatch.setattr(TaskOut, "model_validate", boom)
    assert len(_new(_tasks(50))) == 50


def test_assembly_is_faster_than_per_row_validation() -> None:
    tasks = _tasks(1000)

    def timed(fn) -> float:
        start = time.process_time()
        fn(tasks)
        return time.process_time() - start

    # Warm both paths, then measure them round by round, alternating which one
    # goes first. Measuring one path to completion and then the other put a
    # busy runner's drift — CPU frequency, cache pressure — on whichever ran
    # second, and flipped this red on CI with the fast path 38% behind.
    # Interleaved, the same drift lands on both sides.
    timed(_new)
    timed(_old)
    new_runs: list[float] = []
    old_runs: list[float] = []
    for round_number in range(7):
        if round_number % 2:
            old_runs.append(timed(_old))
            new_runs.append(timed(_new))
        else:
            new_runs.append(timed(_new))
            old_runs.append(timed(_old))
    new, old = min(new_runs), min(old_runs)
    # CPU time, not wall clock: a busy CI worker must not turn the faster path
    # red. Measured ~0.77 (26 ms vs 34 ms of CPU per 1000 rows); the rest is
    # ORM attribute reads.
    assert new < old * 0.95, (new, old)
