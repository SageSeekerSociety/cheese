"""Idempotent legacy replay in ``human.persist_human_message`` batches block
serialization via ``dump_blocks_json``, yielding between chunks while preserving
byte-identical payloads and ``block_ids`` ordering."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent.turn.store import human
from app.domain.block import adapters
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType


def _make_extra_blocks(
    count: int,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    turn_id: uuid.UUID,
    start_seq: int,
) -> list[Block]:
    base_time = datetime(2026, 10, 18, 0, 0, 0, tzinfo=UTC)
    return [
        Block(
            id=uuid.UUID(int=i + 100),
            project_id=project_id,
            conversation_id=conversation_id,
            seq=start_seq + i,
            kind=BlockKind.message if i % 2 == 0 else BlockKind.event,
            author_type=AuthorType.participant if i % 2 == 0 else AuthorType.platform,
            author="alice",
            content=f"bundle-block-{i}",
            reply_to=None,
            mime_type="text/markdown" if i % 5 == 0 else None,
            refs=[],
            turn_id=turn_id,
            meta={"idx": i} if i % 3 == 0 else None,
            created_at=base_time,
        )
        for i in range(count)
    ]


@pytest.mark.anyio
async def test_persist_human_message_legacy_replay_matches_bytes_and_yields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    project_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    turn_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    client_id = "client-replay-batch-001"
    fixed_now = datetime(2026, 10, 18, 1, 0, 0, tzinfo=UTC)

    session = AsyncMock()

    @asynccontextmanager
    async def sessions():
        yield session

    place = SimpleNamespace(
        room=SimpleNamespace(project_id=project_id),
        room_id=topic_id,
        conversation_id=topic_id,
    )
    resolver = SimpleNamespace(conversation=AsyncMock(return_value=place))
    monkeypatch.setattr(human, "PlaceResolver", lambda _s: resolver)

    async def prepare(_s, _place, text: str, _author: str) -> human.HumanPreparation:
        return human.HumanPreparation(
            project=SimpleNamespace(id=project_id),  # type: ignore[arg-type]
            content=text,
            roster=[],
            agent_handles=[],
            named_seats={},
            recipient={"mode": "room"},
        )

    async def refuse(
        _s, _place, _prepared, _recipient, _author: str, _reply_author: str | None
    ) -> human.CallAdmission:
        return human.CallAdmission(addressed=None, refused=None)

    created_on_first_send: list[Block] = []
    seq_counter = 0

    async def fake_append_output(_s, **kwargs) -> Block:
        nonlocal seq_counter
        seq_counter += 1
        block = Block(
            id=uuid.UUID(int=seq_counter),
            project_id=kwargs["project_id"],
            conversation_id=kwargs["conversation_id"],
            seq=seq_counter,
            kind=kwargs["kind"],
            author_type=kwargs["author_type"],
            author=kwargs["author"],
            content=kwargs["content"],
            reply_to=kwargs["reply_to"],
            mime_type=kwargs.get("mime_type"),
            refs=[],
            turn_id=kwargs["turn_id"],
            meta=kwargs.get("meta"),
            created_at=fixed_now,
        )
        created_on_first_send.append(block)
        return block

    monkeypatch.setattr(human, "append_output", fake_append_output)
    monkeypatch.setattr(human.idem, "claim", AsyncMock(return_value=True))
    recorded_results: list[dict] = []

    async def fake_record_result(_s, _key: str, payload: dict) -> None:
        recorded_results.append(payload)

    monkeypatch.setattr(human.idem, "record_result", fake_record_result)

    bundle_calls = 0
    legacy_bundle: list[Block] = []

    async def fake_client_delivery_bundle(
        _s, _room_id: uuid.UUID, *, author: str, client_id: str
    ) -> list[Block]:
        nonlocal bundle_calls
        bundle_calls += 1
        if bundle_calls == 1:
            return []
        assert author == "alice"
        assert client_id == "client-replay-batch-001"
        return legacy_bundle

    monkeypatch.setattr(human, "client_delivery_bundle", fake_client_delivery_bundle)

    # 1. First send with client_id creates the initial message + attachment blocks.
    first = await human.persist_human_message(
        sessions,  # type: ignore[arg-type]
        prepare=prepare,
        refuse=refuse,
        record_effects=AsyncMock(return_value=[]),
        topic_id=topic_id,
        author="alice",
        content="hello idempotent world",
        turn_id=turn_id,
        reply_to=None,
        attachments=[{"path": "uploads/a.png", "mime": "image/png"}],
        client_id=client_id,
    )
    assert first.duplicate is False
    assert len(first.payloads) == 2

    # Populate legacy_blocks for the second send: the exact blocks from the first
    # send plus 1,198 additional blocks (1,200 total -> 3 chunks of 500 -> 2 yields).
    legacy_bundle.extend(created_on_first_send)
    legacy_bundle.extend(
        _make_extra_blocks(
            1_198,
            project_id=project_id,
            conversation_id=topic_id,
            turn_id=turn_id,
            start_seq=len(created_on_first_send) + 1,
        )
    )
    expected_all = [
        BlockOut.model_validate(b).model_dump(mode="json") for b in legacy_bundle
    ]

    real_sleep = asyncio.sleep
    sleep_zero_calls = 0

    async def counting_sleep(delay: float):
        nonlocal sleep_zero_calls
        if delay == 0:
            sleep_zero_calls += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    # 2. Second send with the same client_id hits the legacy_blocks replay branch.
    replay = await human.persist_human_message(
        sessions,  # type: ignore[arg-type]
        prepare=prepare,
        refuse=refuse,
        record_effects=AsyncMock(return_value=[]),
        topic_id=topic_id,
        author="alice",
        content="hello idempotent world",
        turn_id=turn_id,
        reply_to=None,
        attachments=[{"path": "uploads/a.png", "mime": "image/png"}],
        client_id=client_id,
    )

    assert replay.duplicate is True
    assert replay.anchor_id == first.anchor_id
    # 1,200 blocks / 500 chunk size = 3 chunks -> 2 inter-chunk sleep(0) yields.
    assert sleep_zero_calls == 2
    assert replay.block_ids == [b.id for b in legacy_bundle]
    assert replay.block_ids[:2] == first.block_ids
    assert recorded_results[-1]["block_ids"] == [str(b.id) for b in legacy_bundle]

    # First replayed payload (and initial pair) is byte-identical to first send.
    assert json.dumps(replay.payloads[0], sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) == json.dumps(first.payloads[0], sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    assert json.dumps(replay.payloads[:2], sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) == json.dumps(first.payloads, sort_keys=True, ensure_ascii=False).encode("utf-8")
    assert json.dumps(replay.payloads, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) == json.dumps(expected_all, sort_keys=True, ensure_ascii=False).encode("utf-8")
