"""一条消息只落一次 (dogfooding 2026-08): the same hook event reaches the persist
path from two places — the turn's own attribution and the platform-unsolicited
path — and both used to write.

The tell in production is unmistakable: two blocks with the SAME `eid`, 14ms
apart, identical text. `has_any_eid` is a SELECT followed by an INSERT, so two
callers both read "not there" and both insert. Nothing about the check is wrong;
it just cannot be the thing that decides.
"""

import asyncio
import uuid

import pytest

from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import stub_compute


async def _topic(factory) -> uuid.UUID:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="user-1")
        topic = await TopicService(session).create(
            project_id=project.id, title="讨论", created_by="user-1"
        )
        topic_id: uuid.UUID = topic.id
        await session.commit()
    return topic_id


async def _ai_messages(factory, topic_id: uuid.UUID) -> list[str]:
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    return [
        b.content
        for b in blocks
        if b.author_type == AuthorType.ai
        and b.kind == BlockKind.event
        and (b.meta or {}).get("progress")
    ]


@pytest.mark.anyio
async def test_two_writers_of_one_event_land_a_single_message(client, tmp_path):
    factory = client.test_factory  # type: ignore[attr-defined]
    service = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    topic_id = await _topic(factory)

    async def land(unsolicited: bool):
        return await service._persist_assistant_message(
            project_id=(await _project_of(factory, topic_id)),
            topic_id=topic_id,
            text="查到了，根因在只读 token 上。",
            turn_id=uuid.uuid4(),  # the two paths attribute to different turns
            reply_to=None,
            roster=[],
            topic_refs=[],
            eid="e-same-event",
            platform_unsolicited=unsolicited,
        )

    payloads = await asyncio.gather(land(False), land(True))

    assert await _ai_messages(factory, topic_id) == ["查到了，根因在只读 token 上。"]
    # And only the writer that actually landed it gets something to broadcast —
    # otherwise the room renders the message twice off the wire even though the
    # database holds one.
    assert [p for p in payloads if p is not None] != []
    assert len([p for p in payloads if p is not None]) == 1


@pytest.mark.anyio
async def test_a_genuinely_new_event_still_lands(client, tmp_path):
    """The guard must not swallow the next message just because it looks like
    the last one — 「好的」 twice in one turn is two messages."""
    factory = client.test_factory  # type: ignore[attr-defined]
    service = ChatService(
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    topic_id = await _topic(factory)
    project_id = await _project_of(factory, topic_id)

    for eid in ("e-1", "e-2"):
        await service._persist_assistant_message(
            project_id=project_id,
            topic_id=topic_id,
            text="好的",
            turn_id=uuid.uuid4(),
            reply_to=None,
            roster=[],
            topic_refs=[],
            eid=eid,
        )

    assert await _ai_messages(factory, topic_id) == ["好的", "好的"]


async def _project_of(factory, topic_id: uuid.UUID) -> uuid.UUID:
    from app.domain.topic.repositories import TopicRepository

    async with factory() as session:
        topic = await TopicRepository(session).get(topic_id)
        assert topic is not None
        return topic.project_id
