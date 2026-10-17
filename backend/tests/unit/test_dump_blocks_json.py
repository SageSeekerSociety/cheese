"""Chunked block validation and JSON-mode serialization yields between batches
and produces byte-identical output to per-row `BlockOut.model_validate`."""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.api.routes import topics_asks
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType


def _make_blocks(count: int) -> list[Block]:
    conv_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    proj_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    turn_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    base_time = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    return [
        Block(
            id=uuid.UUID(int=i + 1),
            project_id=proj_id,
            conversation_id=conv_id,
            seq=i + 1,
            kind=BlockKind.message if i % 3 else BlockKind.event,
            author_type=AuthorType.participant if i % 2 else AuthorType.platform,
            author=f"user-{i % 17}",
            content=f"block-{i}",
            reply_to=uuid.UUID(int=i) if i and i % 5 == 0 else None,
            mime_type="text/markdown" if i % 7 == 0 else None,
            refs=[f"ref-{i % 11}"] if i % 4 == 0 else [],
            turn_id=turn_id if i % 2 == 0 else None,
            meta={"tool": "Bash", "idx": i} if i % 3 == 0 else None,
            created_at=base_time,
        )
        for i in range(count)
    ]


def test_dump_blocks_json_10k_yields_between_chunks_and_matches_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.block import adapters

    blocks = _make_blocks(10_000)
    expected = [BlockOut.model_validate(b).model_dump(mode="json") for b in blocks]

    real_sleep = asyncio.sleep
    sleep_zero_calls = 0

    async def counting_sleep(delay: float):
        nonlocal sleep_zero_calls
        if delay == 0:
            sleep_zero_calls += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    async def run():
        return await adapters.dump_blocks_json(blocks, chunk=500)

    actual = asyncio.run(run())

    # 10,000 blocks / 500 per chunk = 20 chunks -> 19 inter-chunk sleep(0) yields.
    assert sleep_zero_calls == 19
    assert actual == expected
    assert json.dumps(actual, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) == json.dumps(expected, sort_keys=True, ensure_ascii=False).encode("utf-8")


def test_dump_blocks_json_empty_and_invalid_chunk() -> None:
    from app.domain.block.adapters import dump_blocks_json

    assert asyncio.run(dump_blocks_json([])) == []
    with pytest.raises(ValueError, match="chunk"):
        asyncio.run(dump_blocks_json([], chunk=0))


def test_create_questions_yields_between_block_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _make_blocks(1_200)
    expected = [BlockOut.model_validate(b).model_dump(mode="json") for b in rows]

    topic_id = rows[0].conversation_id
    proj_id = rows[0].project_id
    place = SimpleNamespace(
        project_id=proj_id,
        conversation_id=topic_id,
        room_id=topic_id,
        room=SimpleNamespace(id=topic_id),
    )

    async def fake_place_or_404(_db, _tid):
        return place

    class FakeResolver:
        async def resolve(self, **_kwargs):
            return SimpleNamespace(via="cheese", handle="cheese-seat")

        async def authorize_topic(self, *_args, **_kwargs):
            return None

    class FakeMemberService:
        def __init__(self, _db):
            pass

        async def holds_an_agent_seat(self, _room, _handle):
            return True

    class FakeBroker:
        def __init__(self):
            self.published: list[tuple[str, dict]] = []

        async def publish(self, channel: str, payload: dict):
            self.published.append((channel, payload))

    broker = FakeBroker()
    concurrent_ticks = 0
    serializing = False

    async def fake_ask(_db, **_kwargs):
        nonlocal serializing
        serializing = True
        return rows

    class FakeDb:
        async def commit(self):
            nonlocal serializing
            serializing = False

    monkeypatch.setattr(topics_asks, "place_or_404", fake_place_or_404)
    monkeypatch.setattr(topics_asks, "TopicMemberService", FakeMemberService)
    monkeypatch.setattr(topics_asks, "ask", fake_ask)
    monkeypatch.setattr(topics_asks, "get_broker", lambda: broker)

    async def run():
        nonlocal concurrent_ticks
        done = False

        async def ticker():
            nonlocal concurrent_ticks
            while not done:
                if serializing:
                    concurrent_ticks += 1
                await asyncio.sleep(0)

        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        res = await topics_asks.create_questions(
            topic_id=topic_id,
            body={"request_id": "req-1"},
            db=cast(Any, FakeDb()),
            resolver=cast(Any, FakeResolver()),
        )
        done = True
        await ticker_task
        return res

    response = asyncio.run(run())
    # 1,200 blocks / 500 per chunk = 3 chunks -> 2 yields during serialization.
    assert concurrent_ticks >= 2
    assert response["data"]["blocks"] == expected
    assert len(broker.published) == 1_200
