"""Real PostgreSQL transactions, including independent concurrent writers."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.domain.block.models import Block, BlockKind
from app.domain.living_doc.services import DocumentJournal, content_hash
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered, session_auth_headers
from tests.integration.test_docs import _topic


async def seed(factory):
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        await session.commit()
        return project.root_topic_id


@pytest.mark.anyio
@pytest.mark.parametrize("initial", [None, "初稿"])
async def test_independent_sessions_have_exactly_one_cas_winner(
    business_db_factory, initial
):
    factory = business_db_factory
    room = await seed(factory)
    base = 0
    if initial is not None:
        async with factory() as session:
            await TopicService(session).edit_doc(
                topic_id=room, content=initial, author="alice", expected_version=0
            )
            await session.commit()
        base = 1
    start = asyncio.Event()

    async def write(content):
        async with factory() as session:
            await start.wait()
            try:
                doc, _ = await TopicService(session).edit_doc(
                    topic_id=room,
                    content=content,
                    author="alice",
                    expected_version=base,
                )
                await session.commit()
                return doc.content
            except ConflictError:
                await session.rollback()
                return None

    tasks = [asyncio.create_task(write(text)) for text in ["赢家甲", "赢家乙"]]
    start.set()
    results = await asyncio.gather(*tasks)
    winner = next(value for value in results if value is not None)
    assert results.count(None) == 1
    async with factory() as session:
        doc = await TopicService(session).get_doc(room)
        assert doc.content == winner
        assert doc.doc_version == base + 1
        versions = await DocumentJournal(session).history(room)
        assert len(versions) == base + 1
        assert versions[-1]["content"] == winner
        assert versions[-1]["content_hash"] == content_hash(winner)
        blocks = list(
            await session.scalars(select(Block).where(Block.topic_id == room))
        )
        assert sum(block.kind == BlockKind.doc for block in blocks) == 1
        assert [
            block.content for block in blocks if block.kind == BlockKind.doc_node
        ] == [winner]
        edits = [block for block in blocks if (block.meta or {}).get("action") == "doc"]
        assert len(edits) == base + 1


def test_lost_response_replays_original_receipt_without_second_effect(client):
    room = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    operation = str(uuid.uuid4())
    body = {
        "content": "😀\r\n重复句\r\n重复句",
        "expected_version": 0,
        "operation_id": operation,
    }
    first = client.put(f"/topics/{room}/doc", json=body)
    assert first.status_code == 200
    # Treat the response as lost: recover it after another writer has advanced.
    later = client.put(
        f"/topics/{room}/doc", json={"content": "后来", "expected_version": 1}
    )
    assert later.status_code == 200
    replay = client.put(f"/topics/{room}/doc", json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    queried = client.get(f"/topics/{room}/doc/operations/{operation}")
    assert queried.json() == first.json()
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 2
    history = client.get(f"/topics/{room}/doc/history").json()["data"]["versions"]
    assert len(history) == 2
    assert history[0]["content"] == body["content"]
    assert history[0]["content_hash"] == content_hash(body["content"])
    different = client.put(f"/topics/{room}/doc", json={**body, "content": "别的"})
    assert different.status_code == 409
    assert (
        len(client.get(f"/topics/{room}/doc/history").json()["data"]["versions"]) == 2
    )


def test_restore_adds_new_version_and_replays_without_rewriting_raw(client):
    room = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    raw = "😀\r\n相同句\r\n相同句\r\n"
    for base, content in enumerate([raw, "后来"]):
        assert (
            client.put(
                f"/topics/{room}/doc",
                json={"content": content, "expected_version": base},
            ).status_code
            == 200
        )
    operation = str(uuid.uuid4())
    payload = {"version": 1, "expected_version": 2, "operation_id": operation}
    response = client.post(f"/topics/{room}/doc/restore", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["doc_version"] == 3
    assert response.json()["data"]["content"] == raw
    assert (
        client.post(f"/topics/{room}/doc/restore", json=payload).json()
        == response.json()
    )
    receipt = client.get(f"/topics/{room}/doc/operations/{operation}?action=restore")
    assert receipt.json() == response.json()
    versions = client.get(f"/topics/{room}/doc/history").json()["data"]["versions"]
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert versions[0]["content"] == versions[2]["content"] == raw
    assert versions[2]["previous_version"] == versions[2]["base_version"] == 2


@pytest.mark.anyio
async def test_rollback_leaves_no_document_nodes_event_history_or_claim(
    business_db_factory,
):
    factory = business_db_factory
    room = await seed(factory)
    operation_id = uuid.uuid4()
    async with factory() as session:
        journal = DocumentJournal(session)
        await journal.claim(
            room_id=room,
            actor="alice",
            action="replace",
            operation_id=operation_id,
            payload={"content": "未提交", "expected_version": 0},
        )
        await TopicService(session).edit_doc(
            topic_id=room,
            content="未提交",
            author="alice",
            expected_version=0,
            operation_id=operation_id,
        )
        await session.rollback()
    async with factory() as session:
        assert await TopicService(session).get_doc(room) is None
        assert await DocumentJournal(session).history(room) == []
        assert (
            await DocumentJournal(session).receipt(
                room_id=room, actor="alice", action="replace", operation_id=operation_id
            )
            is None
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Block).where(Block.topic_id == room)
            )
            == 0
        )
