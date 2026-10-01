"""Real PostgreSQL transactions, including independent concurrent writers."""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.domain.block.models import Block, BlockKind
from app.domain.living_doc.delivery import dispatch_pending
from app.domain.living_doc.models import (
    DocumentOperation,
    DocumentRefresh,
    DocumentVersion,
)
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


@pytest.mark.anyio
async def test_platform_brief_seed_has_raw_history_without_contribution_event(
    business_db_factory,
):
    factory = business_db_factory
    room = await seed(factory)
    raw = "# 原稿\r\n😀"
    async with factory() as session:
        topics = TopicService(session)
        topic = await topics.get(room)
        await topics.seed_brief_doc(topic, raw)
        await session.commit()
    async with factory() as session:
        doc = await TopicService(session).get_doc(room)
        assert doc.content == raw
        versions = await DocumentJournal(session).history(room)
        assert len(versions) == 1
        assert versions[0]["content"] == raw
        assert versions[0]["actor"] == "system"
        assert versions[0]["base_version"] == 0
        assert versions[0]["event_id"] is None
        blocks = list(
            await session.scalars(select(Block).where(Block.topic_id == room))
        )
        assert not any((block.meta or {}).get("action") == "doc" for block in blocks)


@pytest.mark.anyio
async def test_database_rejects_history_mutation_and_incomplete_receipts(
    business_db_factory,
):
    factory = business_db_factory
    room = await seed(factory)
    async with factory() as session:
        await TopicService(session).edit_doc(
            topic_id=room, content="不可变😀\r\n", author="alice", expected_version=0
        )
        await session.commit()
    for mutation in [
        update(DocumentVersion).values(content="伪造"),
        delete(DocumentVersion),
    ]:
        async with factory() as session:
            with pytest.raises(IntegrityError, match="history is immutable"):
                await session.execute(mutation.where(DocumentVersion.room_id == room))
            await session.rollback()
    operation_id = uuid.uuid4()
    async with factory() as session:
        await DocumentJournal(session).claim(
            room_id=room,
            actor="alice",
            action="replace",
            operation_id=operation_id,
            payload={"content": "不能提交", "expected_version": 1},
        )
        await TopicService(session).edit_doc(
            topic_id=room,
            content="不能提交",
            author="alice",
            expected_version=1,
        )
        with pytest.raises(IntegrityError, match="must commit its receipt"):
            await session.commit()
        await session.rollback()
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).doc_version == 1
        assert len(await DocumentJournal(session).history(room)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(DocumentOperation))
            == 0
        )
        assert len(await DocumentJournal(session).refreshes(room)) == 1


@pytest.mark.anyio
async def test_durable_refresh_recovers_failure_and_keeps_reconnect_cursor(
    business_db_factory,
):
    factory = business_db_factory
    room = await seed(factory)
    async with factory() as session:
        await TopicService(session).edit_doc(
            topic_id=room, content="已提交", author="alice", expected_version=0
        )
        await session.commit()
    frames = []

    async def unavailable(channel, message):
        raise OSError("offline subscriber transport")

    async with factory() as session:
        with pytest.raises(OSError, match="offline subscriber transport"):
            await dispatch_pending(session, unavailable)
        await session.rollback()
    async with factory() as session:
        assert (await session.scalar(select(DocumentRefresh))).dispatched_at is None
        assert (await TopicService(session).get_doc(room)).content == "已提交"

    async def publish(channel, message):
        frames.append((channel, message))

    async with factory() as session:
        assert await dispatch_pending(session, publish) == 1
    async with factory() as session:
        assert await dispatch_pending(session, publish) == 0
        hints = await DocumentJournal(session).refreshes(room, after=0)
        assert frames == [(str(room), hints[0])]
        assert hints[0]["cursor"] == 1
        assert hints[0]["content_hash"] == content_hash("已提交")
        assert await DocumentJournal(session).refreshes(room, after=1) == []


@pytest.mark.anyio
@pytest.mark.parametrize("different", [False, True])
async def test_independent_operation_claims_apply_once(business_db_factory, different):
    factory = business_db_factory
    room = await seed(factory)
    operation_id = uuid.uuid4()
    start = asyncio.Event()

    async def write(content):
        async with factory() as session:
            await start.wait()
            journal = DocumentJournal(session)
            try:
                operation = await journal.claim(
                    room_id=room,
                    actor="alice",
                    action="replace",
                    operation_id=operation_id,
                    payload={"content": content, "expected_version": 0},
                )
                if operation.receipt is not None:
                    return operation.receipt
                doc, _ = await TopicService(session).edit_doc(
                    topic_id=room,
                    content=content,
                    author="alice",
                    expected_version=0,
                    operation_id=operation_id,
                )
                receipt = {"content": doc.content, "version": doc.doc_version}
                await journal.finish(operation, receipt)
                await session.commit()
                return receipt
            except ConflictError:
                await session.rollback()
                return None

    tasks = [
        asyncio.create_task(write(text))
        for text in ["一次", "不同" if different else "一次"]
    ]
    start.set()
    results = await asyncio.gather(*tasks)
    if different:
        assert results.count(None) == 1
    else:
        assert results[0] == results[1]
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).doc_version == 1
        assert len(await DocumentJournal(session).history(room)) == 1
        assert len(await DocumentJournal(session).refreshes(room)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(DocumentOperation))
            == 1
        )
        blocks = list(
            await session.scalars(select(Block).where(Block.topic_id == room))
        )
        assert sum((block.meta or {}).get("action") == "doc" for block in blocks) == 1
        assert sum(block.kind == BlockKind.doc_node for block in blocks) == 1


@pytest.mark.anyio
async def test_completed_receipt_and_claim_identity_cannot_be_rewritten(
    business_db_factory,
):
    factory = business_db_factory
    room = await seed(factory)
    operation_id = uuid.uuid4()
    async with factory() as session:
        journal = DocumentJournal(session)
        operation = await journal.claim(
            room_id=room,
            actor="alice",
            action="replace",
            operation_id=operation_id,
            payload={"content": "原回执"},
        )
        await journal.finish(operation, {"version": 1, "content": "原回执"})
        await session.commit()
    for fields in [
        {"receipt": {"version": 2}},
        {"actor": "someone"},
        {"fingerprint": "f" * 64},
    ]:
        async with factory() as session:
            with pytest.raises(IntegrityError, match="receipt is immutable"):
                await session.execute(
                    update(DocumentOperation)
                    .where(DocumentOperation.operation_id == operation_id)
                    .values(**fields)
                )
            await session.rollback()
    async with factory() as session:
        assert await DocumentJournal(session).receipt(
            room_id=room, actor="alice", action="replace", operation_id=operation_id
        ) == {"version": 1, "content": "原回执"}


def test_operation_requires_real_owner_and_ignores_claimed_author(client, monkeypatch):
    from app.core.config import settings
    from tests.conftest import seed_user

    room = _topic(client)
    seed_user(client, "outsider")
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    operation = str(uuid.uuid4())
    payload = {
        "content": "保存一次",
        "expected_version": 0,
        "operation_id": operation,
        "author": "owner",
    }
    assert client.put(f"/topics/{room}/doc", json=payload).status_code == 401
    outsider = session_auth_headers("outsider")
    assert (
        client.put(f"/topics/{room}/doc", json=payload, headers=outsider).status_code
        == 403
    )
    owner = session_auth_headers("owner")
    saved = client.put(
        f"/topics/{room}/doc", json={**payload, "author": "outsider"}, headers=owner
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["author"] == "owner"
    assert (
        client.get(
            f"/topics/{room}/doc/operations/{operation}", headers=outsider
        ).status_code
        == 403
    )
    assert (
        client.get(f"/topics/{room}/doc/operations/{operation}", headers=owner).json()
        == saved.json()
    )
    hints = client.get(f"/topics/{room}/doc/refreshes", headers=owner).json()["data"]
    assert hints["cursor"] == 1
    assert len(hints["refreshes"]) == 1


def test_committed_response_failure_replays_one_persisted_effect(client, monkeypatch):
    from app.api.routes import living_docs

    room = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    operation = str(uuid.uuid4())
    payload = {
        "content": "已落库😀\r\n",
        "expected_version": 0,
        "operation_id": operation,
    }
    original = living_docs.get_broker().publish

    async def failed_publish(channel, message):
        raise OSError("injected after commit")

    monkeypatch.setattr(living_docs.get_broker(), "publish", failed_publish)
    with pytest.raises(OSError, match="injected after commit"):
        client.put(f"/topics/{room}/doc", json=payload)
    monkeypatch.setattr(living_docs.get_broker(), "publish", original)
    queried = client.get(f"/topics/{room}/doc/operations/{operation}")
    assert queried.status_code == 200
    replay = client.put(f"/topics/{room}/doc", json=payload)
    assert replay.status_code == 200
    assert queried.json() == replay.json()
    assert client.get(f"/topics/{room}/doc").json()["data"]["doc_version"] == 1
    assert (
        len(client.get(f"/topics/{room}/doc/history").json()["data"]["versions"]) == 1
    )
    assert (
        len(client.get(f"/topics/{room}/doc/refreshes").json()["data"]["refreshes"])
        == 1
    )
