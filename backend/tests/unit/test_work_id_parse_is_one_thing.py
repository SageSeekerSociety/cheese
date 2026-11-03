"""Reply target UUID parsing in human turn storage shares ``parse_work_id``.

``human.persist_human_message`` parses ``reply_to`` with the same UUID rule used
for work attribution, credential scope and deliverable reuse. Keeping a fourth
private ``_parse_uuid`` in ``human.py`` lets reply-target parsing drift if the
accepted UUID text format changes. Both object identity and caller-level
``reply_to`` threading behavior are pinned here.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import work_context
from app.domain.agent.turn.store import human
from app.domain.block.models import BlockKind
from app.domain.event_author import AuthorType

_ID = uuid.UUID("934bfa5a-193d-4d51-a29b-96771eaf4aa6")


async def _run_persist_human_message(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reply_to: str | None,
) -> tuple[human.StoredHuman, AsyncMock, list[str | None]]:
    topic_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = AsyncMock()

    @asynccontextmanager
    async def sessions():
        yield session

    place = SimpleNamespace(
        room=SimpleNamespace(project_id=project_id),
        task=None,
        room_id=topic_id,
        conversation_id=topic_id,
    )
    resolver = SimpleNamespace(conversation=AsyncMock(return_value=place))
    monkeypatch.setattr(human, "PlaceResolver", lambda _s: resolver)

    reply_lookup = AsyncMock(
        return_value=SimpleNamespace(
            id=_ID,
            conversation_id=topic_id,
            author="alice",
            kind=BlockKind.message,
            author_type=AuthorType.participant,
            content="parent",
            mime_type=None,
        )
    )
    monkeypatch.setattr(human, "message_reply_target", reply_lookup)

    seen_reply_authors: list[str | None] = []

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
        _s, _place, _prepared, _recipient, _author: str, reply_author: str | None
    ) -> human.CallAdmission:
        seen_reply_authors.append(reply_author)
        return human.CallAdmission(addressed=None, refused=None)

    async def fake_append_output(_s, **kwargs):
        return SimpleNamespace(
            id=uuid.uuid4(),
            conversation_id=kwargs["conversation_id"],
            seq=1,
            kind=kwargs["kind"],
            author_type=kwargs["author_type"],
            author=kwargs["author"],
            content=kwargs["content"],
            reply_to=kwargs["reply_to"],
            mime_type=kwargs.get("mime_type"),
            refs=[],
            turn_id=kwargs["turn_id"],
            meta=kwargs.get("meta"),
            reactions=[],
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(human, "append_output", fake_append_output)

    stored = await human.persist_human_message(
        sessions,  # type: ignore[arg-type]
        prepare=prepare,
        refuse=refuse,
        record_effects=AsyncMock(return_value=[]),
        topic_id=topic_id,
        author="bob",
        content="hello",
        turn_id=uuid.uuid4(),
        reply_to=reply_to,
    )
    return stored, reply_lookup, seen_reply_authors


def test_human_store_reuses_the_one_parse_work_id() -> None:
    assert human.parse_work_id is work_context.parse_work_id
    assert not hasattr(human, "_parse_uuid")


@pytest.mark.parametrize(
    "raw, expected",
    [
        (str(_ID), _ID),
        ("", None),
        (None, None),
        ("not-a-uuid", None),
    ],
)
def test_human_and_work_context_parse_work_id_agree(
    raw: str | None, expected: uuid.UUID | None
) -> None:
    assert human.parse_work_id(raw) == work_context.parse_work_id(raw) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw, expected_reply_to, expected_reply_author",
    [
        (str(_ID), _ID, "alice"),
        ("", None, None),
        (None, None, None),
        ("not-a-uuid", None, None),
    ],
)
async def test_persist_human_message_threads_only_a_valid_reply_uuid(
    monkeypatch: pytest.MonkeyPatch,
    raw: str | None,
    expected_reply_to: uuid.UUID | None,
    expected_reply_author: str | None,
) -> None:
    stored, reply_lookup, seen_reply_authors = await _run_persist_human_message(
        monkeypatch, reply_to=raw
    )

    assert stored.payloads[0]["reply_to"] == (
        str(expected_reply_to) if expected_reply_to else None
    )
    assert seen_reply_authors == [expected_reply_author]
    if expected_reply_to is None:
        reply_lookup.assert_not_awaited()
    else:
        reply_lookup.assert_awaited_once()


@pytest.mark.anyio
async def test_persist_human_message_delegates_reply_to_to_parse_work_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(human, "parse_work_id", lambda _raw: None)

    stored, reply_lookup, seen_reply_authors = await _run_persist_human_message(
        monkeypatch, reply_to=str(_ID)
    )

    assert stored.payloads[0]["reply_to"] is None
    assert seen_reply_authors == [None]
    reply_lookup.assert_not_awaited()
