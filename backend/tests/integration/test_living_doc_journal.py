"""Real PostgreSQL transactions, including independent concurrent writers."""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.doc_store import store
from app.core.errors import ConflictError
from app.domain.block.documents import DocumentWriter
from app.domain.block.models import Block, BlockKind
from app.domain.living_doc.models import (
    DocumentNode,
    DocumentOperation,
    DocumentVersion,
)
from app.domain.living_doc.services import DocumentJournal, Documents, content_hash
from app.domain.project.services import ProjectService
from app.domain.room_task.services import TaskService
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.services import TopicService
from tests.integration.conftest import registered, session_auth_headers
from tests.integration.test_docs import _topic
from tests.support.living_doc import document_of


async def seed(factory):
    """A task, whose living document these tests write."""
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        task = await TopicService(session).create_task(
            room_id=project.root_topic_id, created_by="alice"
        )
        await session.commit()
        return task.id


async def task_doc(session, task):
    """The task's document, made the way an editor's ticket makes it."""
    place = await TopicService(session).place_or_404(task)
    return await Documents(session).get(
        await TaskService(session).ensure_document(place.task)
    )


async def history(session, task):
    doc = await task_doc(session, task)
    return await DocumentJournal(session).history(doc.id)


async def stored(factory, task, content, *actors, operation=None):
    """One store from the collaboration service, as its own transaction."""
    async with factory() as session:
        result = await store(
            session,
            await task_doc(session, task),
            state=b"yjs",
            content=content,
            actors=list(actors) or ["alice"],
            operation=operation,
        )
        await session.commit()
        return result.answer


def _doc(client, task) -> str:
    """The task's document, as its routes address it."""
    return f"/documents/{document_of(client, task)}"


@pytest.mark.anyio
async def test_concurrent_stores_each_record_a_version_in_order(business_db_factory):
    factory = business_db_factory
    task = await seed(factory)
    await stored(factory, task, "初稿")
    start = asyncio.Event()

    async def write(content):
        await start.wait()
        return await stored(factory, task, content)

    tasks = [asyncio.create_task(write(text)) for text in ["第二版", "第三版"]]
    start.set()
    await asyncio.gather(*tasks)
    async with factory() as session:
        doc = await task_doc(session, task)
        versions = await history(session, task)
        assert [row["version"] for row in versions] == [1, 2, 3]
        assert doc.version == 3
        assert doc.content == versions[-1]["content"]
        assert versions[-1]["content_hash"] == content_hash(doc.content)
        assert {row["content"] for row in versions[1:]} == {"第二版", "第三版"}


@pytest.mark.anyio
async def test_a_store_that_changes_no_text_records_no_version(business_db_factory):
    factory = business_db_factory
    task = await seed(factory)
    await stored(factory, task, "")
    await stored(factory, task, "同一句")
    await stored(factory, task, "同一句", "bob")
    async with factory() as session:
        versions = await history(session, task)
        assert [row["content"] for row in versions] == ["同一句"]
        doc = await task_doc(session, task)
        assert await DocumentJournal(session).state(doc.id) == b"yjs"


@pytest.mark.anyio
async def test_a_store_names_everyone_whose_changes_it_holds(business_db_factory):
    factory = business_db_factory
    task = await seed(factory)
    await stored(factory, task, "第一句", "alice")
    await stored(factory, task, "第一句\n\n第二句", "bob", "alice")
    async with factory() as session:
        versions = await history(session, task)
        assert versions[-1]["actor"] == "bob"
        events = [
            block
            for block in await session.scalars(
                select(Block).where(Block.kind == BlockKind.event)
            )
            if (block.meta or {}).get("action") == "doc"
        ]
        assert "<@bob>" in events[-1].content and "<@alice>" in events[-1].content


def test_lost_response_replays_original_receipt_without_second_effect(client):
    task = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    operation = str(uuid.uuid4())
    body = {
        "content": "😀\r\n重复句\r\n重复句",
        "expected_version": 0,
        "operation_id": operation,
    }
    first = client.put(_doc(client, task), json=body)
    assert first.status_code == 200
    # Treat the response as lost: recover it after another writer has advanced.
    later = client.put(
        _doc(client, task),
        json={"content": "后来", "expected_version": 1},
    )
    assert later.status_code == 200
    replay = client.put(_doc(client, task), json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    queried = client.get(f"{_doc(client, task)}/operations/{operation}")
    assert queried.json() == first.json()
    assert client.get(_doc(client, task)).json()["data"]["doc_version"] == 2
    history = client.get(f"{_doc(client, task)}/history").json()["data"]["versions"]
    assert len(history) == 2
    assert history[0]["content"] == body["content"]
    assert history[0]["content_hash"] == content_hash(body["content"])
    different = client.put(_doc(client, task), json={**body, "content": "别的"})
    assert different.status_code == 409
    assert (
        len(client.get(f"{_doc(client, task)}/history").json()["data"]["versions"]) == 2
    )


def test_history_lists_the_latest_versions_first_and_pages_back(client):
    task = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    for base, content in enumerate(["一", "二", "三"]):
        assert (
            client.put(
                _doc(client, task),
                json={"content": content, "expected_version": base},
            ).status_code
            == 200
        )
    latest = client.get(f"{_doc(client, task)}/history?newest=true").json()["data"]
    assert [row["content"] for row in latest["versions"]] == ["三", "二", "一"]
    before = latest["versions"][0]["version"]
    older = client.get(
        f"{_doc(client, task)}/history?newest=true&before={before}"
    ).json()["data"]
    assert [row["content"] for row in older["versions"]] == ["二", "一"]
    assert older["versions"][0]["actor"] == "owner"
    last = client.get(f"{_doc(client, task)}/history?newest=true&limit=1").json()[
        "data"
    ]
    assert [row["content"] for row in last["versions"]] == ["三"]


def test_restore_adds_new_version_and_replays_without_rewriting_raw(client):
    task = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    raw = "😀\r\n相同句\r\n相同句\r\n"
    for base, content in enumerate([raw, "后来"]):
        assert (
            client.put(
                _doc(client, task),
                json={"content": content, "expected_version": base},
            ).status_code
            == 200
        )
    operation = str(uuid.uuid4())
    payload = {"version": 1, "expected_version": 2, "operation_id": operation}
    response = client.post(f"{_doc(client, task)}/restore", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["doc_version"] == 3
    assert response.json()["data"]["content"] == raw
    assert (
        client.post(f"{_doc(client, task)}/restore", json=payload).json()
        == response.json()
    )
    receipt = client.get(f"{_doc(client, task)}/operations/{operation}?action=restore")
    assert receipt.json() == response.json()
    versions = client.get(f"{_doc(client, task)}/history").json()["data"]["versions"]
    assert [row["version"] for row in versions] == [1, 2, 3]
    assert versions[0]["content"] == versions[2]["content"] == raw
    assert versions[2]["previous_version"] == versions[2]["base_version"] == 2


@pytest.mark.anyio
async def test_rollback_leaves_no_document_nodes_event_history_or_claim(
    business_db_factory,
):
    factory = business_db_factory
    task = await seed(factory)
    operation_id = uuid.uuid4()
    async with factory() as session:
        doc = await task_doc(session, task)
        journal = DocumentJournal(session)
        await journal.claim(
            document_id=doc.id,
            actor="alice",
            action="replace",
            operation_id=operation_id,
            payload={"content": "未提交", "expected_version": 0},
        )
        await DocumentWriter(session, summarize_doc_change).record(
            doc, content="未提交", actors=["alice"], operation_id=operation_id
        )
        await session.rollback()
    async with factory() as session:
        assert (await task_doc(session, task)).version == 0
        for table in (DocumentVersion, DocumentOperation, DocumentNode):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Block)
                .where(Block.conversation_id == task)
            )
            == 0
        )


@pytest.mark.anyio
async def test_platform_brief_seed_has_raw_history_without_contribution_event(
    business_db_factory,
):
    """A new project's overview written from words it already had: the history
    keeps them exactly, as the platform's, and no conversation is told
    somebody contributed."""
    factory = business_db_factory
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name="P", owner_handle="alice")
        await session.commit()
    raw = "# 原稿\r\n😀"
    async with factory() as session:
        projects = ProjectService(session)
        await projects.seed_overview(await projects.get_or_404(project.id), raw)
        await session.commit()
    async with factory() as session:
        projects = ProjectService(session)
        doc = await projects.overview_document(await projects.get_or_404(project.id))
        assert doc.content == raw
        versions = await DocumentJournal(session).history(doc.id)
        assert len(versions) == 1
        assert versions[0]["content"] == raw
        assert versions[0]["actor"] == "system"
        assert versions[0]["base_version"] == 0
        assert versions[0]["event_id"] is None
        blocks = list(
            await session.scalars(select(Block).where(Block.project_id == project.id))
        )
        assert not any((block.meta or {}).get("action") == "doc" for block in blocks)


@pytest.mark.anyio
async def test_database_rejects_history_mutation_and_incomplete_receipts(
    business_db_factory,
):
    factory = business_db_factory
    task = await seed(factory)
    await stored(factory, task, "不可变😀\r\n")
    for mutation in [
        update(DocumentVersion).values(content="伪造"),
        delete(DocumentVersion),
    ]:
        async with factory() as session:
            with pytest.raises(IntegrityError, match="history is immutable"):
                doc = await task_doc(session, task)
                await session.execute(
                    mutation.where(DocumentVersion.document_id == doc.id)
                )
            await session.rollback()
    operation_id = uuid.uuid4()
    async with factory() as session:
        doc = await task_doc(session, task)
        await DocumentJournal(session).claim(
            document_id=doc.id,
            actor="alice",
            action="replace",
            operation_id=operation_id,
            payload={"content": "不能提交", "expected_version": 1},
        )
        await DocumentWriter(session, summarize_doc_change).record(
            doc, content="不能提交", actors=["alice"]
        )
        with pytest.raises(IntegrityError, match="must commit its receipt"):
            await session.commit()
        await session.rollback()
    async with factory() as session:
        assert (await task_doc(session, task)).version == 1
        assert len(await history(session, task)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(DocumentOperation))
            == 0
        )


@pytest.mark.anyio
@pytest.mark.parametrize("different", [False, True])
async def test_independent_operation_claims_apply_once(business_db_factory, different):
    factory = business_db_factory
    task = await seed(factory)
    operation_id = uuid.uuid4()
    start = asyncio.Event()

    async def write(content):
        await start.wait()
        try:
            return await stored(
                factory,
                task,
                content,
                operation={
                    "actor": "alice",
                    "action": "replace",
                    "operation_id": str(operation_id),
                    "payload": {"content": content, "expected_version": 0},
                },
            )
        except ConflictError:
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
        assert (await task_doc(session, task)).version == 1
        assert len(await history(session, task)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(DocumentOperation))
            == 1
        )
        blocks = list(
            await session.scalars(select(Block).where(Block.conversation_id == task))
        )
        assert sum((block.meta or {}).get("action") == "doc" for block in blocks) == 1
        doc = await task_doc(session, task)
        assert len(await TopicService(session).doc_nodes(doc)) == 1


@pytest.mark.anyio
async def test_completed_receipt_and_claim_identity_cannot_be_rewritten(
    business_db_factory,
):
    factory = business_db_factory
    task = await seed(factory)
    operation_id = uuid.uuid4()
    async with factory() as session:
        doc = await task_doc(session, task)
        journal = DocumentJournal(session)
        operation = await journal.claim(
            document_id=doc.id,
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
            document_id=doc.id,
            actor="alice",
            action="replace",
            operation_id=operation_id,
        ) == {"version": 1, "content": "原回执"}


def test_operation_requires_real_owner_and_ignores_claimed_author(client, monkeypatch):
    from app.core.config import settings
    from tests.conftest import seed_user

    task = _topic(client)
    seed_user(client, "outsider")
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    operation = str(uuid.uuid4())
    payload = {
        "content": "保存一次",
        "expected_version": 0,
        "operation_id": operation,
        "author": "owner",
    }
    assert client.put(_doc(client, task), json=payload).status_code == 401
    outsider = session_auth_headers("outsider")
    assert (
        client.put(_doc(client, task), json=payload, headers=outsider).status_code
        == 403
    )
    owner = session_auth_headers("owner")
    saved = client.put(
        _doc(client, task),
        json={**payload, "author": "outsider"},
        headers=owner,
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["author"] == "owner"
    assert (
        client.get(
            f"{_doc(client, task)}/operations/{operation}",
            headers=outsider,
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{_doc(client, task)}/operations/{operation}",
            headers=owner,
        ).json()
        == saved.json()
    )
    assert (
        client.get(f"{_doc(client, task)}/history", headers=outsider).status_code == 403
    )


def test_committed_response_failure_replays_one_persisted_effect(client, monkeypatch):
    from app.domain.living_doc import collab

    task = _topic(client)
    client.headers.update(session_auth_headers("owner"))
    operation = str(uuid.uuid4())
    payload = {
        "content": "已落库😀\r\n",
        "expected_version": 0,
        "operation_id": operation,
    }
    original = collab.tell

    async def failed_tell(document_id, frame):
        raise OSError("injected after commit")

    # The store committed; telling the open editors failed, so the writer
    # never heard.
    monkeypatch.setattr(collab, "tell", failed_tell)
    assert client.put(_doc(client, task), json=payload).status_code != 200
    monkeypatch.setattr(collab, "tell", original)
    queried = client.get(f"{_doc(client, task)}/operations/{operation}")
    assert queried.status_code == 200
    replay = client.put(_doc(client, task), json=payload)
    assert replay.status_code == 200
    assert queried.json() == replay.json()
    assert client.get(_doc(client, task)).json()["data"]["doc_version"] == 1
    assert (
        len(client.get(f"{_doc(client, task)}/history").json()["data"]["versions"]) == 1
    )
