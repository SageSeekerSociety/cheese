"""Independent database sessions prove recovery without an active room turn."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError
from app.domain.doc_ai.models import DocAiAttempt, DocAiProposal
from app.domain.doc_ai.schemas import AskResult, CompletionUsage, ProposalResult
from app.domain.doc_ai.services import DocAiService
from app.domain.topic.services import TopicService
from tests.integration.test_living_doc_journal import seed


async def request(factory, kind="propose"):
    room = await seed(factory)
    async with factory() as session:
        doc, _ = await TopicService(session).edit_doc(
            topic_id=room,
            content="原文😀",
            author="alice",
            expected_version=0,
        )
        row = await DocAiService(session).create(
            project_id=doc.project_id,
            room_id=room,
            document_id=doc.id,
            actor="user:1",
            kind=kind,
            question="解释这段",
            base_version=1,
            source=doc.content,
            selection={"start": 0, "end": 6} if kind == "propose" else None,
            binding={"model": "test-route", "supply": "gateway"},
        )
        await session.commit()
        return room, row.id


def usage(upstream_id):
    return CompletionUsage(
        model="test-route",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.01,
        upstream_id=upstream_id,
    )


@pytest.mark.anyio
async def test_expired_lease_late_result_retains_spend_without_second_proposal(
    business_db_factory,
):
    factory = business_db_factory
    room, request_id = await request(factory)
    start = datetime.now(UTC)
    async with factory() as session:
        old = await DocAiService(session).claim_next(now=start, seconds=10)
        await session.commit()
    # A fresh worker recovers an expired lease, not a new request.
    async with factory() as session:
        new = await DocAiService(session).claim_next(now=start + timedelta(seconds=11))
        await session.commit()
    assert old.request_id == new.request_id == request_id
    assert new.generation == old.generation + 1
    async with factory() as session:
        assert not await DocAiService(session).settle(
            old,
            result=ProposalResult(answer="旧结果", replacement="错误"),
            usage=usage("old"),
            now=start + timedelta(seconds=12),
        )
        await session.commit()
    async with factory() as session:
        assert await DocAiService(session).settle(
            new,
            result=ProposalResult(answer="新结果", replacement="新提案"),
            usage=usage("new"),
            now=start + timedelta(seconds=13),
        )
        await session.commit()
    async with factory() as session:
        assert not await DocAiService(session).settle(
            new,
            result=ProposalResult(answer="新结果", replacement="新提案"),
            usage=usage("new"),
            now=start + timedelta(seconds=14),
        )
        await session.commit()
        proposals = list(await session.scalars(select(DocAiProposal)))
        assert len(proposals) == 1
        assert proposals[0].replacement == "新提案"
        attempts = list(
            await session.scalars(
                select(DocAiAttempt).order_by(DocAiAttempt.generation)
            )
        )
        assert [attempt.usage["upstream_id"] for attempt in attempts] == ["old", "new"]
        row = await DocAiService(session).get(room, request_id)
        assert row.answer == "新结果" and row.state == "succeeded"
        doc = await TopicService(session).get_doc(room)
        assert doc.content == "原文😀" and doc.doc_version == 1


@pytest.mark.anyio
async def test_cancel_fences_late_completion_and_survives_new_session(
    business_db_factory,
):
    factory = business_db_factory
    room, request_id = await request(factory)
    async with factory() as session:
        lease = await DocAiService(session).claim_next()
        await session.commit()
    async with factory() as session:
        await DocAiService(session).cancel(room, request_id)
        await session.commit()
    async with factory() as session:
        assert not await DocAiService(session).settle(
            lease,
            result=ProposalResult(answer="晚回", replacement="不能应用"),
            usage=usage("cancelled"),
        )
        await session.commit()
        assert (await DocAiService(session).get(room, request_id)).state == "cancelled"
        assert await DocAiService(session).claim_next() is None
        assert (
            await session.scalar(select(func.count()).select_from(DocAiProposal)) == 0
        )
        assert (await TopicService(session).get_doc(room)).doc_version == 1


@pytest.mark.anyio
async def test_competing_workers_only_one_claims_pending_request(business_db_factory):
    factory = business_db_factory
    await request(factory)
    start = asyncio.Event()

    async def claim():
        async with factory() as session:
            await start.wait()
            lease = await DocAiService(session).claim_next()
            await session.commit()
            return lease

    workers = [asyncio.create_task(claim()) for _ in range(2)]
    start.set()
    leases = await asyncio.gather(*workers)
    assert sum(lease is not None for lease in leases) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DocAiAttempt)) == 1


@pytest.mark.anyio
async def test_result_rollback_recovers_and_ask_never_creates_proposal(
    business_db_factory,
):
    factory = business_db_factory
    room, request_id = await request(factory, "ask")
    async with factory() as session:
        lease = await DocAiService(session).claim_next()
        await session.commit()
    async with factory() as session:
        assert await DocAiService(session).settle(
            lease, result=AskResult(answer="解释"), usage=usage("answer")
        )
        await session.rollback()
    async with factory() as session:
        assert (await DocAiService(session).get(room, request_id)).state == "running"
        assert await DocAiService(session).settle(
            lease, result=AskResult(answer="解释"), usage=usage("answer")
        )
        await session.commit()
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DocAiProposal)) == 0
        )
        assert (await TopicService(session).get_doc(room)).doc_version == 1
        with pytest.raises(ConflictError):
            await DocAiService(session).settle(
                lease, result=AskResult(answer="解释"), usage=usage("different")
            )


@pytest.mark.anyio
async def test_failure_is_durable_and_not_acceptible(business_db_factory):
    factory = business_db_factory
    room, request_id = await request(factory)
    async with factory() as session:
        lease = await DocAiService(session).claim_next()
        await session.commit()
    async with factory() as session:
        assert await DocAiService(session).settle(
            lease, result=None, usage=None, error="provider unavailable"
        )
        await session.commit()
    async with factory() as session:
        row = await DocAiService(session).get(room, request_id)
        assert row.state == "failed" and row.error == "provider unavailable"
        assert await DocAiService(session).claim_next() is None
        assert (
            await session.scalar(select(func.count()).select_from(DocAiProposal)) == 0
        )
