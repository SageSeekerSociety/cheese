"""Stored byte ranges are the only executable selection contract."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, ValidationError
from app.domain.block.doc_selection import DocumentSelections
from app.domain.block.models import Block, BlockKind
from app.domain.doc_ai.acceptance import ProposalAcceptance
from app.domain.doc_ai.models import DocAiProposal
from app.domain.doc_ai.schemas import AcceptIn, CompletionUsage, ProposalResult
from app.domain.doc_ai.services import DocAiService
from app.domain.living_doc.services import content_hash
from app.domain.topic.services import TopicService
from tests.integration.test_living_doc_journal import seed

RAW = "# 标题\r\n\r\n重复😀é句\r\n\r\n重复😀é句\r\n\r\n末段不动\r\n"


async def proposal(factory):
    room = await seed(factory)
    async with factory() as session:
        doc, _ = await TopicService(session).edit_doc(
            topic_id=room,
            content=RAW,
            author="alice",
            expected_version=0,
        )
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


@pytest.mark.anyio
async def test_accept_preserves_outside_raw_and_replay_never_downgrades_current(
    business_db_factory,
):
    factory = business_db_factory
    room, proposal_id, span, nodes = await proposal(factory)
    body = AcceptIn(operation_id=uuid.uuid4(), expected_version=1, revision=1)
    async with factory() as session:
        receipt, notice = await ProposalAcceptance(session).apply(
            room_id=room,
            proposal_id=proposal_id,
            body=body,
            verified_actor="user:1",
            author="alice",
        )
        assert notice is not None
        await session.commit()
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
        await TopicService(session).edit_doc(
            topic_id=room, content="后来", author="alice", expected_version=2
        )
        await session.commit()
    async with factory() as session:
        replay, notice = await ProposalAcceptance(session).apply(
            room_id=room,
            proposal_id=proposal_id,
            body=body,
            verified_actor="user:1",
            author="alice",
        )
        assert replay == receipt and notice is None
        assert (await TopicService(session).get_doc(room)).doc_version == 3
        with pytest.raises(ConflictError):
            await ProposalAcceptance(session).apply(
                room_id=room,
                proposal_id=proposal_id,
                body=body.model_copy(update={"revision": 2}),
                verified_actor="user:1",
                author="alice",
            )


@pytest.mark.anyio
async def test_two_acceptors_with_different_ops_have_only_one_effect(
    business_db_factory,
):
    factory = business_db_factory
    room, proposal_id, _, _ = await proposal(factory)
    start = asyncio.Event()

    async def accept(actor):
        async with factory() as session:
            await start.wait()
            try:
                receipt, _ = await ProposalAcceptance(session).apply(
                    room_id=room,
                    proposal_id=proposal_id,
                    body=AcceptIn(
                        operation_id=uuid.uuid4(), expected_version=1, revision=1
                    ),
                    verified_actor=actor,
                    author="alice",
                )
                await session.commit()
                return receipt
            except ConflictError:
                await session.rollback()
                return None

    tasks = [asyncio.create_task(accept(actor)) for actor in ["user:1", "user:2"]]
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
    async with factory() as session:
        await TopicService(session).edit_doc(
            topic_id=room, content="别人已改", author="alice", expected_version=1
        )
        await session.commit()
    async with factory() as session:
        with pytest.raises(ConflictError):
            await ProposalAcceptance(session).apply(
                room_id=room,
                proposal_id=proposal_id,
                body=AcceptIn(
                    operation_id=uuid.uuid4(), expected_version=1, revision=1
                ),
                verified_actor="user:1",
                author="alice",
            )
        await session.rollback()
    async with factory() as session:
        assert (await TopicService(session).get_doc(room)).content == "别人已改"
        assert (await session.get(DocAiProposal, proposal_id)).state == "pending"
