"""Durable leases fence results, not upstream network calls.

Every mutation stays in the caller's transaction. A worker commits a lease,
releases the transaction for completion, then settles in a fresh session.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.domain.doc_ai.models import DocAiAttempt, DocAiProposal, DocAiRequest
from app.domain.doc_ai.schemas import AskResult, CompletionUsage, ProposalResult
from app.domain.living_doc.services import content_hash


@dataclass(frozen=True)
class Lease:
    request_id: uuid.UUID
    generation: int
    kind: str
    question: str
    source: str
    selection: dict | None
    binding: dict
    project_id: uuid.UUID
    room_id: uuid.UUID
    actor: str = ""


class DocAiService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        document_id: uuid.UUID,
        actor: str,
        kind: str,
        question: str,
        base_version: int,
        source: str,
        selection: dict | None,
        binding: dict,
    ) -> DocAiRequest:
        row = DocAiRequest(
            project_id=project_id,
            room_id=room_id,
            document_id=document_id,
            actor=actor,
            kind=kind,
            question=question,
            base_version=base_version,
            source=source,
            source_hash=content_hash(source),
            selection=selection,
            binding=binding,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, room_id: uuid.UUID, request_id: uuid.UUID) -> DocAiRequest:
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(
                DocAiRequest.id == request_id,
                DocAiRequest.room_id == room_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("没有这份文档 AI 请求")
        return row

    async def claim_next(
        self, *, now: datetime | None = None, seconds: int = 120
    ) -> Lease | None:
        if seconds <= 0:
            raise ValueError("lease duration must be positive")
        now = now or datetime.now(UTC)
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(
                or_(
                    DocAiRequest.state == "pending",
                    and_(
                        DocAiRequest.state == "running", DocAiRequest.lease_until <= now
                    ),
                )
            )
            .order_by(DocAiRequest.created_at, DocAiRequest.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        row.generation += 1
        row.state = "running"
        row.lease_until = now + timedelta(seconds=seconds)
        self.session.add(
            DocAiAttempt(
                request_id=row.id,
                generation=row.generation,
                started_at=now,
            )
        )
        await self.session.flush()
        return Lease(
            row.id,
            row.generation,
            row.kind,
            row.question,
            row.source,
            row.selection,
            row.binding,
            row.project_id,
            row.room_id,
            row.actor,
        )

    async def ready_to_invoke(self, lease: Lease) -> bool:
        row = await self.get(lease.room_id, lease.request_id)
        return (
            row.state == "running"
            and row.generation == lease.generation
            and row.lease_until is not None
            and row.lease_until > datetime.now(UTC)
        )

    async def mark_invoking(self, lease: Lease) -> bool:
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(DocAiRequest.id == lease.request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or not await self.ready_to_invoke(lease):
            return False
        # Commit before HTTP, so a crash with a lost response still gets metered.
        row.meter_after = datetime.now(UTC)
        await self.session.flush()
        return True

    async def claim_meter(self, *, now: datetime | None = None) -> Lease | None:
        now = now or datetime.now(UTC)
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(DocAiRequest.meter_after <= now)
            .order_by(DocAiRequest.meter_after, DocAiRequest.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        # Keep the row scan-able for delayed upstream spend, even after success.
        # The shared ledger checkpoint, not this schedule, deduplicates billing.
        row.meter_after = now + timedelta(minutes=5)
        await self.session.flush()
        return Lease(
            row.id,
            row.generation,
            row.kind,
            row.question,
            row.source,
            row.selection,
            row.binding,
            row.project_id,
            row.room_id,
            row.actor,
        )

    async def cancel(self, room_id: uuid.UUID, request_id: uuid.UUID) -> DocAiRequest:
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(
                DocAiRequest.id == request_id,
                DocAiRequest.room_id == room_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("没有这份文档 AI 请求")
        if row.state == "succeeded":
            raise ConflictError("请求已完成，不能撤销已生成的结果")
        if row.state not in ("failed", "cancelled"):
            row.state = "cancelled"
            row.generation += 1
            row.lease_until = None
        await self.session.flush()
        return row

    async def settle(
        self,
        lease: Lease,
        *,
        result: AskResult | ProposalResult | None,
        usage: CompletionUsage | None,
        error: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        row = await self.session.scalar(
            select(DocAiRequest)
            .where(
                DocAiRequest.id == lease.request_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            return False
        attempt = await self.session.scalar(
            select(DocAiAttempt)
            .where(
                DocAiAttempt.request_id == lease.request_id,
                DocAiAttempt.generation == lease.generation,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None:
            raise ConflictError("没有这次文档 AI 尝试")
        receipt = usage.model_dump(mode="json") if usage else None
        if attempt.finished_at is not None:
            if attempt.usage != receipt or attempt.error != error:
                raise ConflictError("同次 completion 已保存不同用量或错误")
            return False
        attempt.finished_at = now
        attempt.usage = receipt
        attempt.error = error
        current = (
            row.state == "running"
            and row.generation == lease.generation
            and row.lease_until is not None
            and row.lease_until > now
        )
        if not current:
            await self.session.flush()
            return False
        if error is not None or result is None:
            row.state = "failed"
            row.error = error or "模型没有返回结果"
        else:
            if row.kind == "propose":
                if not isinstance(result, ProposalResult) or row.selection is None:
                    raise ConflictError("提案结果没有完整的替换范围和内容")
                self.session.add(
                    DocAiProposal(
                        request_id=row.id,
                        replacement=result.replacement,
                    )
                )
            elif isinstance(result, ProposalResult):
                raise ConflictError("问答不能生成执行型提案")
            row.answer = result.answer
            row.state = "succeeded"
        row.lease_until = None
        await self.session.flush()
        return True

    async def proposal_id(self, request_id: uuid.UUID) -> uuid.UUID | None:
        return await self.session.scalar(
            select(DocAiProposal.id).where(DocAiProposal.request_id == request_id)
        )

    async def list_requests(self, room_id: uuid.UUID, actor: str) -> list[DocAiRequest]:
        return list(
            await self.session.scalars(
                select(DocAiRequest)
                .where(
                    DocAiRequest.room_id == room_id,
                    DocAiRequest.actor == actor,
                )
                .order_by(DocAiRequest.created_at.desc())
                .limit(100)
            )
        )

    async def proposal(
        self, room_id: uuid.UUID, proposal_id: uuid.UUID, *, lock: bool = False
    ):
        query = (
            select(DocAiProposal, DocAiRequest)
            .join(
                DocAiRequest,
                DocAiRequest.id == DocAiProposal.request_id,
            )
            .where(DocAiProposal.id == proposal_id, DocAiRequest.room_id == room_id)
        )
        if lock:
            query = query.with_for_update()
        pair = (
            await self.session.execute(query.execution_options(populate_existing=True))
        ).first()
        if pair is None:
            raise NotFoundError("没有这份文档 AI 提案")
        return pair
