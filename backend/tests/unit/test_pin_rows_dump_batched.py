"""`pin_rows` dumps every pinned block in one batched pass, and the rows stay
byte-identical to the per-pin loop it replaced.

Two rules, stated before the code:

- 200 pins come back exactly as one `BlockOut.model_validate(...).model_dump(
  mode="json")` per pin produced them, in `of_room`'s newest-first order. The
  batched dump must not reorder the rows or change one field of a block.
- The event loop is released between chunks. `dump_blocks_json` validates 500
  rows per chunk and yields between them, so a channel past one chunk lets
  other work run. Putting the per-pin `model_validate` back holds the loop for
  the whole list: the ticker below never gets a turn and the yield assertion
  goes red.

The fake `Pins` returns `of_room`'s answer as it is ordered — the newest pin
first, `Pin.id` breaking ties — because the ordering is the repository's job
and `pin_rows` must carry it through untouched.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from app.api import topic_pin_rows
from app.domain.block import adapters
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType

_CONVERSATION = uuid.UUID("11111111-1111-1111-1111-111111111111")
_PROJECT = uuid.UUID("22222222-2222-2222-2222-222222222222")
_TURN = uuid.UUID("33333333-3333-3333-3333-333333333333")
_BASE_TIME = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def _pairs(count: int) -> list[tuple[Any, Block]]:
    """`Pins.of_room`: the channel's pins with what they pin, newest first."""
    pairs: list[tuple[Any, Block]] = []
    for i in range(count):
        block = Block(
            id=uuid.UUID(int=i + 1),
            project_id=_PROJECT,
            conversation_id=_CONVERSATION,
            seq=i + 1,
            kind=BlockKind.message if i % 3 else BlockKind.event,
            author_type=AuthorType.participant if i % 2 else AuthorType.platform,
            author=f"user-{i % 17}",
            content=f"block-{i}",
            reply_to=uuid.UUID(int=i) if i and i % 5 == 0 else None,
            mime_type="text/markdown" if i % 7 == 0 else None,
            refs=[f"ref-{i % 11}"] if i % 4 == 0 else [],
            turn_id=_TURN if i % 2 == 0 else None,
            meta={"tool": "Bash", "idx": i} if i % 3 == 0 else None,
            created_at=_BASE_TIME,
        )
        pin = SimpleNamespace(
            id=uuid.UUID(int=10_000 + i),
            pinned_by=f"user-{i % 5}",
            pinned_at=_BASE_TIME - timedelta(minutes=i),
        )
        pairs.append((pin, block))
    return pairs


def _per_pin_rows(pairs: list[tuple[Any, Block]]) -> list[dict]:
    """What the loop built before the batched dump replaced it."""
    return [
        {
            "pinned_by": pin.pinned_by,
            "pinned_at": pin.pinned_at.isoformat(),
            "block": BlockOut.model_validate(block).model_dump(mode="json"),
        }
        for pin, block in pairs
    ]


def _fake_pins(monkeypatch, pairs: list[tuple[Any, Block]]) -> None:
    class FakePins:
        def __init__(self, _db, *, team):
            assert team is not None

        async def of_room(self, _room_id):
            # No await inside: a yield here would hand the ticker a turn that
            # the code under test did not give it.
            return pairs

    monkeypatch.setattr(topic_pin_rows, "Pins", FakePins)


def _rows(pairs: list[tuple[Any, Block]]) -> list[dict]:
    return asyncio.run(topic_pin_rows.pin_rows(cast(Any, object()), uuid.uuid4()))


def test_200_pins_match_the_per_pin_rows_byte_for_byte(monkeypatch) -> None:
    pairs = _pairs(200)
    _fake_pins(monkeypatch, pairs)

    batches: list[int] = []
    real_validate = adapters._BLOCK_LIST_ADAPTER.validate_python

    def tracking_validate(batch, *args, **kwargs):
        batches.append(len(batch))
        return real_validate(batch, *args, **kwargs)

    monkeypatch.setattr(
        adapters._BLOCK_LIST_ADAPTER, "validate_python", tracking_validate
    )

    actual = _rows(pairs)
    expected = _per_pin_rows(pairs)

    assert actual == expected
    # Byte-for-byte, not just `==`: a field the batched adapter serialises
    # differently (a default it fills in, an enum it spells differently) would
    # still compare equal dict-wise under a loose reader but not on the wire.
    assert json.dumps(actual, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) == json.dumps(expected, sort_keys=True, ensure_ascii=False).encode("utf-8")
    # `of_room`'s newest-first order is carried through, pin by pin.
    assert [row["block"]["id"] for row in actual] == [
        str(block.id) for _, block in pairs
    ]
    assert [row["pinned_at"] for row in actual] == [
        pin.pinned_at.isoformat() for pin, _ in pairs
    ]
    # One batch of 200, not 200 validations of one: the per-pin loop never
    # touches this adapter at all, so `[]` is what this assertion would see.
    assert batches == [200]


def test_a_channel_past_one_chunk_gives_the_loop_time_between_batches(
    monkeypatch,
) -> None:
    # 1,200 blocks / 500 per chunk = three chunks, so two turns of the loop.
    pairs = _pairs(1_200)
    _fake_pins(monkeypatch, pairs)

    async def run():
        ticks = 0
        done = False

        async def ticker():
            nonlocal ticks
            while not done:
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        rows = await topic_pin_rows.pin_rows(cast(Any, object()), uuid.uuid4())
        done = True
        await task
        return rows, ticks

    rows, ticks = asyncio.run(run())

    assert ticks >= 2
    assert rows == _per_pin_rows(pairs)
