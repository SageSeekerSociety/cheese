"""Chunked task-row validation and JSON-mode serialization yields between
batches and produces byte-identical output to per-row `TaskOut.model_validate`."""

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.room_task.models import Task, TaskStatus, TaskTitleSource
from app.domain.room_task.schemas import TaskOut

PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ROOM_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
BASE_TIME = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def _make_tasks(count: int) -> list[Task]:
    return [
        Task(
            id=uuid.UUID(int=i + 1),
            project_id=PROJECT_ID,
            room_id=ROOM_ID,
            number=i + 1 if i % 9 else None,
            title=f"任务 {i}",
            title_source=TaskTitleSource.human if i % 4 else TaskTitleSource.auto,
            status=TaskStatus.open if i % 2 else TaskStatus.closed,
            owner_handle=f"user-{i % 7}" if i % 5 else None,
            reviewer_handle="reviewer" if i % 3 else None,
            reporter_handle=None,
            contributor_handles=[f"helper-{i}"] if i % 6 == 0 else [],
            created_by=f"creator-{i % 3}",
            created_by_assistant=i % 5 == 0,
            branch_name=f"task/{i}" if i % 4 == 0 else None,
            base_branch="main",
            pr_number=1000 + i if i % 4 == 0 else None,
            pr_url=f"https://example.test/pull/{1000 + i}" if i % 4 == 0 else None,
            delivered_head=None,
            agent_handle="cheese" if i % 2 else None,
            document_id=None,
            accepted_by=None,
            accepted_at=None,
            closed_at=None,
            upgraded_from_block_id=None,
            source_block_id=None,
            conclusion=f"done {i}" if i % 8 == 0 else None,
            created_at=BASE_TIME,
            updated_at=BASE_TIME,
        )
        for i in range(count)
    ]


def _expected(tasks: list[Task]) -> list[dict]:
    return [TaskOut.model_validate(task).model_dump(mode="json") for task in tasks]


def _as_bytes(rows: list[dict]) -> bytes:
    return json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")


def test_dump_task_rows_json_100_rows_matches_per_row() -> None:
    from app.domain.room_task.adapters import dump_task_rows_json

    tasks = _make_tasks(100)

    actual = asyncio.run(dump_task_rows_json(tasks))

    assert actual == _expected(tasks)


def test_dump_task_rows_json_1000_rows_yields_every_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.room_task import adapters

    tasks = _make_tasks(1_000)
    expected = _expected(tasks)

    real_sleep = asyncio.sleep
    sleep_zero_calls = 0
    validated_at_each_yield: list[int] = []
    validated_so_far = 0
    real_validate = adapters._TASK_LIST_ADAPTER.validate_python

    def tracking_validate(batch, *args, **kwargs):
        nonlocal validated_so_far
        res = real_validate(batch, *args, **kwargs)
        validated_so_far += len(res)
        return res

    async def counting_sleep(delay: float):
        nonlocal sleep_zero_calls
        if delay == 0:
            sleep_zero_calls += 1
            validated_at_each_yield.append(validated_so_far)
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(
        adapters._TASK_LIST_ADAPTER, "validate_python", tracking_validate
    )

    actual = asyncio.run(adapters.dump_task_rows_json(tasks, chunk=500))

    # 1,000 rows / 500 per chunk = 2 chunks -> 1 inter-chunk sleep(0) yield,
    # with validation progressing by 500 rows before it.
    assert sleep_zero_calls == 1
    assert validated_at_each_yield == [500]
    assert actual == expected
    assert _as_bytes(actual) == _as_bytes(expected)


def test_dump_task_rows_json_1200_rows_keeps_the_partial_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full chunks followed by a short tail are the shape a room list has:
    dropping the tail must not pass."""
    from app.domain.room_task import adapters

    tasks = _make_tasks(1_200)
    expected = _expected(tasks)

    real_sleep = asyncio.sleep
    yields = 0
    validated_at_each_yield: list[int] = []
    validated_so_far = 0
    real_validate = adapters._TASK_LIST_ADAPTER.validate_python

    def tracking_validate(batch, *args, **kwargs):
        nonlocal validated_so_far
        res = real_validate(batch, *args, **kwargs)
        validated_so_far += len(res)
        return res

    async def counting_sleep(delay: float):
        nonlocal yields
        if delay == 0:
            yields += 1
            validated_at_each_yield.append(validated_so_far)
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)
    monkeypatch.setattr(
        adapters._TASK_LIST_ADAPTER, "validate_python", tracking_validate
    )

    actual = asyncio.run(adapters.dump_task_rows_json(tasks, chunk=500))

    assert len(actual) == len(tasks)
    assert yields == 2
    assert validated_at_each_yield == [500, 1_000]
    assert actual == expected


def test_dump_task_rows_json_honours_a_non_default_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chunk argument, not a hardcoded 500, decides where the helper yields."""
    from app.domain.room_task import adapters

    tasks = _make_tasks(20)
    expected = _expected(tasks)

    real_sleep = asyncio.sleep
    yields = 0

    async def counting_sleep(delay: float):
        nonlocal yields
        if delay == 0:
            yields += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    actual = asyncio.run(adapters.dump_task_rows_json(tasks, chunk=7))

    # 20 rows / 7 per chunk = 3 chunks -> 2 inter-chunk yields.
    assert yields == 2
    assert actual == expected


def test_dump_task_rows_json_empty() -> None:
    from app.domain.room_task.adapters import dump_task_rows_json

    assert asyncio.run(dump_task_rows_json([])) == []
