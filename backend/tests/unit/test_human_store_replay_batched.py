"""``human.persist_human_message`` batches block serialization via
``dump_blocks_json`` and replays committed deliveries from ``idempotency_keys``
without scanning ``blocks``."""

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
from app.domain.block.models import Block
from app.domain.block.schemas import BlockOut


@pytest.mark.anyio
async def test_persist_human_message_replays_from_idempotency_store(
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
    claimed = False
    recorded_results: dict[str, dict] = {}

    async def fake_claim(_s, key: str, **_kwargs) -> bool:
        nonlocal claimed
        if claimed:
            return False
        claimed = True
        return True

    async def fake_record_result(_s, key: str, payload: dict) -> None:
        recorded_results[key] = payload

    async def fake_stored_result(_s, key: str) -> dict | None:
        return recorded_results.get(key)

    monkeypatch.setattr(human.idem, "claim", fake_claim)
    monkeypatch.setattr(human.idem, "record_result", fake_record_result)
    monkeypatch.setattr(human.idem, "stored_result", fake_stored_result)

    real_sleep = asyncio.sleep
    sleep_zero_calls = 0

    async def counting_sleep(delay: float):
        nonlocal sleep_zero_calls
        if delay == 0:
            sleep_zero_calls += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    attachments = [
        {"path": f"uploads/a-{i}.png", "mime": "image/png"} for i in range(1199)
    ]
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
        attachments=attachments,
        client_id=client_id,
    )
    assert first.duplicate is False
    assert len(first.payloads) == 1200
    assert sleep_zero_calls == 2
    expected = [
        BlockOut.model_validate(b).model_dump(mode="json")
        for b in created_on_first_send
    ]
    assert json.dumps(first.payloads, sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )

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
        attachments=attachments,
        client_id=client_id,
    )
    assert replay.duplicate is True
    assert replay.anchor_id == first.anchor_id
    assert replay.block_ids == first.block_ids
    assert replay.payloads == first.payloads
