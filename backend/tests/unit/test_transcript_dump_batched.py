"""The transcript's step list goes through the chunked batch dumper.

``GET /topics/{id}/transcript`` used to validate and serialize every step with
``BlockOut.model_validate(b).model_dump(mode="json")`` inside one list
comprehension: a 120-step page is 120 synchronous Pydantic constructions with no
yield to the event loop. It now calls ``dump_blocks_json`` (one TypeAdapter per
500 rows, yielding between chunks), then strips each payload with
``without_output`` as before.

Two halves are pinned here:

* the page is byte-identical to the per-row expression it replaced, and
* the batch path is what actually runs, on both the paged branch and the
  whole-history branch. Both tests record the page handed to the dumper, so a
  body that batches only one of the two branches fails: per-row and batched
  payloads are equal by design, and a paged page (limit ≤ 200) is always a
  single 500-row chunk, so values alone cannot tell them apart. The second test
  also counts the dumper's ``asyncio.sleep(0)`` yields across a whole-history
  page: 1,200 rows are three chunks and therefore two yields, while a per-row
  body yields none.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import topics_transcript
from app.domain.agent.step_output import without_output
from app.domain.block import adapters
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.event_author import AuthorType

CONVERSATION_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PROJECT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
TURN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
BASE_TIME = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def _blocks(count: int, *, with_output: bool = False) -> list[Block]:
    """``count`` 现场 event rows, shaped like the ones the repo pages through."""
    meta = {"tool": "Bash", "platform": False}
    if with_output:
        meta = {**meta, "output": "x" * 8, "output_bytes": 8}
    return [
        Block(
            id=uuid.UUID(int=i + 1),
            project_id=PROJECT_ID,
            conversation_id=CONVERSATION_ID,
            seq=i + 1,
            kind=BlockKind.event,
            author_type=AuthorType.platform,
            author="cheese",
            content=f"step-{i}",
            reply_to=None,
            mime_type=None,
            refs=[],
            turn_id=TURN_ID,
            meta=dict(meta),
            created_at=BASE_TIME,
        )
        for i in range(count)
    ]


def _per_row_reference(blocks: list[Block]) -> list[dict[str, Any]]:
    """The expression the route used before the batch dumper."""
    return [
        without_output(BlockOut.model_validate(b).model_dump(mode="json"))
        for b in blocks
    ]


def _json_bytes(items: list[dict[str, Any]]) -> bytes:
    return json.dumps(items, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _patch_route_dependencies(
    monkeypatch: pytest.MonkeyPatch, blocks: list[Block]
) -> None:
    """Stand in for the DB: only the paging and provenance calls the route makes."""

    class FakeBlockRepository:
        def __init__(self, db: Any) -> None:
            self._db = db

        async def page_for_topic(self, conversation_id, **kwargs):
            return SimpleNamespace(items=blocks, has_more=False)

        async def list_for_topic(self, conversation_id, **kwargs):
            return blocks

    async def fake_place(db, topic_id):
        return SimpleNamespace(conversation_id=CONVERSATION_ID)

    async def fake_actor_in_place(resolver, place):
        return None

    async def no_records(*args, **kwargs):
        return []

    async def no_turn_starts(*args, **kwargs):
        return {}

    monkeypatch.setattr(topics_transcript, "BlockRepository", FakeBlockRepository)
    monkeypatch.setattr(topics_transcript, "place_or_404", fake_place)
    monkeypatch.setattr(topics_transcript, "_actor_in_place", fake_actor_in_place)
    monkeypatch.setattr(topics_transcript, "for_site", no_records)
    monkeypatch.setattr(topics_transcript, "turn_starts", no_turn_starts)


def _call_route(blocks: list[Block], *, limit: int | None) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        return await topics_transcript.topic_transcript(
            CONVERSATION_ID, None, None, limit=limit, before=None, author=None
        )

    return asyncio.run(run())


def _record_batches(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record each page handed to the dumper, still dumping it for real."""
    real = topics_transcript.dump_blocks_json
    sizes: list[int] = []

    async def recording(blocks, *args, **kwargs):
        sizes.append(len(blocks))
        return await real(blocks, *args, **kwargs)

    monkeypatch.setattr(topics_transcript, "dump_blocks_json", recording)
    return sizes


def test_120_step_page_is_byte_identical_to_the_per_row_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = _blocks(120, with_output=True)
    _patch_route_dependencies(monkeypatch, blocks)
    batches = _record_batches(monkeypatch)

    payload = _call_route(blocks, limit=120)
    actual = payload["data"]["data"]
    expected = _per_row_reference(blocks)

    # The paged branch — the 120-step page this change is about — goes through
    # the dumper as one page. Without this a regression that batches only the
    # whole-history branch (limit=None) would still pass, because per-row and
    # batched payloads are equal by design.
    assert batches == [120]
    assert payload["data"]["total"] == 120
    assert actual == expected
    assert _json_bytes(actual) == _json_bytes(expected)
    # `without_output` still strips the kept tail and keeps its size.
    assert all("output" not in (item["meta"] or {}) for item in actual)
    assert all((item["meta"] or {})["output_bytes"] == 8 for item in actual)


def test_whole_history_page_yields_between_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = _blocks(1_200)
    _patch_route_dependencies(monkeypatch, blocks)
    batches = _record_batches(monkeypatch)

    real_sleep = asyncio.sleep
    yields = 0

    async def counting_sleep(delay: float):
        nonlocal yields
        if delay == 0:
            yields += 1
        return await real_sleep(delay)

    monkeypatch.setattr(adapters.asyncio, "sleep", counting_sleep)

    payload = _call_route(blocks, limit=None)

    # 1,200 rows / 500 per chunk = three chunks -> two inter-chunk yields. The
    # per-row list comprehension this replaced yields none, so a revert fails
    # here rather than passing silently.
    assert batches == [1_200]
    assert yields == 2
    assert payload["data"]["total"] == 1_200
    assert payload["data"]["data"] == _per_row_reference(blocks)
