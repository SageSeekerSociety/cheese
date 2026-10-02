"""Stored byte ranges are the only executable selection contract."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.api.doc_store import store
from app.core.errors import ConflictError, ValidationError
from app.domain.block.doc_selection import DocumentSelections
from app.domain.block.models import Block, BlockKind
from app.domain.doc_ai import acceptance
from app.domain.doc_ai.acceptance import ProposalAcceptance
from app.domain.doc_ai.models import DocAiProposal
from app.domain.doc_ai.schemas import AcceptIn, CompletionUsage, ProposalResult
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.models import DocumentOperation
from app.domain.living_doc.services import DocumentJournal, content_hash
from app.domain.topic.services import TopicService
from tests.integration.test_living_doc_journal import seed, stored
from tests.support.living_doc import write_doc

RAW = "# 标题\r\n\r\n重复😀é句\r\n\r\n重复😀é句\r\n\r\n末段不动\r\n"


async def proposal(factory):
    room = await seed(factory)
    async with factory() as session:
        doc, _ = await write_doc(session, room, RAW)
        nodes = list(
            await session.scalars(
                select(Block)
                .where(
                    Block.struct_parent == doc.id,
                    Block.kind == BlockKind.doc_node,
                )
                .order_by(Block.struct_order)
            )
        )
        raw = RAW.encode("utf-8")
        selected = "重复😀é句".encode()
        start = raw.rindex(selected)
        selection = {
            "node_id": str(nodes[2].id),
            "start": start,
            "end": start + len(selected),
            "exact_hash": content_hash(selected.decode()),
        }
        await DocumentSelections(session).snapshot(
            room_id=room,
            document_id=doc.id,
            base_version=1,
            selection=selection,
        )
        row = await DocAiService(session).create(
            project_id=doc.project_id,
            room_id=room,
            document_id=doc.id,
            actor="user:1",
            kind="propose",
            question="改第二段",
            base_version=1,
            source=doc.content,
            selection=selection,
            binding={"model": "test-route", "supply": "gateway"},
        )
        await session.commit()
    async with factory() as session:
        lease = await DocAiService(session).claim_next()
        await session.commit()
    async with factory() as session:
        await DocAiService(session).settle(
            lease,
            result=ProposalResult(
                answer="仅修改第二次出现", replacement="新😀句\r\n仍在单块"
            ),
            usage=CompletionUsage(
                model="test-route",
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.01,
                upstream_id="p",
            ),
        )
        await session.commit()
        item = await session.scalar(
            select(DocAiProposal).where(DocAiProposal.request_id == row.id)
        )
        return room, item.id, selection, [node.id for node in nodes]


async def accept(factory, room, proposal_id, body, actor="user:1"):
    """Accept the way the route and the collaboration service do together: the
    route checks the proposal against the version it was made from and works
    out the new document; the service applies it only while the live document
    still reads the proposal's source (one replace at a time, which the room
    lock stands in for here) and stores it with the operation; the store marks
    the proposal accepted in the same transaction."""
    operation = {
        "actor": actor,
        "action": acceptance.ACTION,
        "operation_id": str(body.operation_id),
        "payload": acceptance.operation_payload(proposal_id, body),
    }
    async with factory() as session:
        replayed = await DocumentJournal(session).replay(
            room_id=room,
            actor=actor,
            action=acceptance.ACTION,
            operation_id=body.operation_id,
            payload=operation["payload"],
        )
        if replayed is not None:
            return replayed
        source, content = await ProposalAcceptance(session).prepare(
            room_id=room, proposal_id=proposal_id, body=body
        )
        doc = await TopicService(session).get_doc(room)
        if doc.doc_version != body.expected_version:
            raise ConflictError("提案或文档版本已经变化")
    async with factory() as session:
        try:
            await DocumentJournal(session).lock(room)
            if (await TopicService(session).get_doc(room)).content != source:
                raise ConflictError("实况文档已经被改过了")
            result = await store(
                session,
                room,
                state=b"yjs",
                content=content,
                actors=["alice"],
                operation=operation,
            )
            await session.commit()
            return result.answer
        except ConflictError:
            await session.rollback()
            raise


@pytest.mark.anyio
async def test_accept_preserves_outside_raw_and_replay_never_downgrades_current(
    business_db_factory,
):
    factory = business_db_factory
    room, proposal_id, span, nodes = await proposal(factory)
    body = AcceptIn(operation_id=uuid.uuid4(), expected_version=1, revision=1)
    receipt = await accept(factory, room, proposal_id, body)
    expected = (
        RAW.encode()[: span["start"]]
        + "新😀句\r\n仍在单块".encode()
        + RAW.encode()[span["end"] :]
    )
    assert receipt["content"].encode() == expected
    assert receipt["doc_version"] == 2
    async with factory() as session:
        unchanged = list(
            await session.scalars(
                select(Block.id).where(Block.id.in_([nodes[0], nodes[1], nodes[3]]))
            )
        )
        assert set(unchanged) == {nodes[0], nodes[1], nodes[3]}
    await stored(factory, room, "后来")
    replay = await accept(factory, room, proposal_id, body)
    assert replay == receipt
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).doc_version == 3
    with pytest.raises(ConflictError):
        await accept(
            factory, room, proposal_id, body.model_copy(update={"revision": 2})
        )


@pytest.mark.anyio
async def test_two_acceptors_with_different_ops_have_only_one_effect(
    business_db_factory,
):
    factory = business_db_factory
    room, proposal_id, _, _ = await proposal(factory)
    start = asyncio.Event()

    async def accept_as(actor):
        await start.wait()
        try:
            return await accept(
                factory,
                room,
                proposal_id,
                AcceptIn(operation_id=uuid.uuid4(), expected_version=1, revision=1),
                actor,
            )
        except ConflictError:
            return None

    tasks = [asyncio.create_task(accept_as(actor)) for actor in ["user:1", "user:2"]]
    start.set()
    results = await asyncio.gather(*tasks)
    assert sum(result is not None for result in results) == 1
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).doc_version == 2
        edits = await session.scalar(
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == room,
                Block.kind == BlockKind.event,
            )
        )
        assert edits == 2


@pytest.mark.anyio
async def test_distinct_same_base_proposals_have_one_canonical_effect(
    business_db_factory,
):
    factory = business_db_factory
    room, first_id, span, nodes = await proposal(factory)
    async with factory() as session:
        doc = await TopicService(session).get_doc(room)
        request = await DocAiService(session).create(
            project_id=doc.project_id,
            room_id=room,
            document_id=doc.id,
            actor="user:2",
            kind="propose",
            question="另一份同基线提案",
            base_version=1,
            source=RAW,
            selection=span,
            binding={"model": "test-route", "supply": "gateway"},
        )
        await session.commit()
    async with factory() as session:
        lease = await DocAiService(session).claim_next()
        assert lease.request_id == request.id
        await session.commit()
    async with factory() as session:
        await DocAiService(session).settle(
            lease,
            result=ProposalResult(answer="另一个候选", replacement="另一😀句"),
            usage=CompletionUsage(
                model="test-route",
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.01,
                upstream_id="second-proposal",
            ),
        )
        await session.commit()
        second_id = await DocAiService(session).proposal_id(request.id)
        assert second_id != first_id
    start = asyncio.Event()
    operations = {item: uuid.uuid4() for item in [first_id, second_id]}

    async def accept_one(item):
        await start.wait()
        try:
            return await accept(
                factory,
                room,
                item,
                AcceptIn(operation_id=operations[item], expected_version=1, revision=1),
            )
        except ConflictError:
            return None

    tasks = [asyncio.create_task(accept_one(item)) for item in [first_id, second_id]]
    start.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    assert results.count(None) == 1
    winner = next(result for result in results if result is not None)
    async with factory() as session:
        doc = await TopicService(session).get_doc(room)
        assert doc.doc_version == 2 and doc.content == winner["content"]
        versions = await DocumentJournal(session).history(room)
        assert len(versions) == 2 and versions[-1]["content"] == winner["content"]
        items = list(
            await session.scalars(
                select(DocAiProposal).where(DocAiProposal.id.in_([first_id, second_id]))
            )
        )
        assert sorted(item.state for item in items) == ["accepted", "pending"]
        accepted = next(item for item in items if item.state == "accepted")
        assert str(accepted.id) == winner["proposal_id"]
        expected = (
            RAW.encode()[: span["start"]]
            + accepted.replacement.encode()
            + RAW.encode()[span["end"] :]
        )
        assert doc.content.encode() == expected
        assert set(
            await session.scalars(
                select(Block.id).where(Block.id.in_([nodes[0], nodes[1], nodes[3]]))
            )
        ) == {nodes[0], nodes[1], nodes[3]}
        claims = list(
            await session.scalars(
                select(DocumentOperation).where(
                    DocumentOperation.room_id == room,
                    DocumentOperation.action == "ai-accept",
                )
            )
        )
        assert len(claims) == 1 and claims[0].receipt == winner
        events = await session.scalar(
            select(func.count())
            .select_from(Block)
            .where(Block.topic_id == room, Block.kind == BlockKind.event)
        )
        assert events == 2


@pytest.mark.anyio
async def test_stale_base_and_wrong_node_leave_no_effects(business_db_factory):
    factory = business_db_factory
    room, proposal_id, span, nodes = await proposal(factory)
    async with factory() as session:
        doc = await TopicService(session).get_doc(room)
        with pytest.raises(ValidationError):
            await DocumentSelections(session).snapshot(
                room_id=room,
                document_id=doc.id,
                base_version=1,
                selection={**span, "node_id": str(nodes[1])},
            )
        await session.rollback()
    await stored(factory, room, "别人已改")
    with pytest.raises(ConflictError):
        await accept(
            factory,
            room,
            proposal_id,
            AcceptIn(operation_id=uuid.uuid4(), expected_version=1, revision=1),
        )
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).content == "别人已改"
        assert (await session.get(DocAiProposal, proposal_id)).state == "pending"
