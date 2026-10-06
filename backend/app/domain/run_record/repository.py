"""运行记录的读写。"""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import with_keys
from app.domain.run_record.models import RunRecord


class RunRecordRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID | None,
        conversation_id: uuid.UUID | None,
        content: str,
        meta: dict | None,
        turn_id: uuid.UUID | None = None,
        seat: str | None = None,
        record_id: uuid.UUID | None = None,
    ) -> RunRecord:
        """`meta` is what `notice()` builds; its `event_type` and `severity`
        become the row's `kind` and `severity`. `record_id` is for the few
        records whose id something else already holds (a delivery ledger
        event that names the record it was announced by)."""
        meta = with_keys(meta, content=content) or {}
        record = RunRecord(
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            seat=seat,
            kind=str(meta.get("event_type") or "unknown"),
            severity=str(meta.get("severity") or "info"),
            content=content,
            meta=meta,
        )
        if record_id is not None:
            record.id = record_id
        self._session.add(record)
        await self._session.flush()
        return record

    async def restate(
        self, record_id: uuid.UUID, *, content: str, meta: dict
    ) -> RunRecord | None:
        """Say again, in place, what a record says: the third retry of the same
        request, the wait that ended. `meta` is merged over what is there."""
        record = await self._session.get(RunRecord, record_id)
        if record is None:
            return None
        merged = with_keys({**(record.meta or {}), **meta}, content=content) or {}
        record.content = content
        record.meta = merged
        record.severity = str(merged.get("severity") or record.severity)
        await self._session.flush()
        return record

    async def running(self, turn_id: uuid.UUID, kind: str) -> list[uuid.UUID]:
        """The turn's records of this kind still saying their news is under way
        (``meta.state == "running"``), oldest first."""
        rows = await self._session.scalars(
            select(RunRecord.id)
            .where(
                RunRecord.turn_id == turn_id,
                RunRecord.kind == kind,
                RunRecord.meta["state"].as_string() == "running",
            )
            .order_by(RunRecord.created_at)
        )
        return list(rows)

    async def of_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
    ) -> list[RunRecord]:
        """The conversation's records between two moments, oldest first."""
        stmt = select(RunRecord).where(RunRecord.conversation_id == conversation_id)
        if since is not None:
            stmt = stmt.where(RunRecord.created_at >= since)
        if until is not None:
            stmt = stmt.where(RunRecord.created_at <= until)
        rows = await self._session.scalars(
            stmt.order_by(RunRecord.created_at.desc()).limit(limit)
        )
        return list(reversed(list(rows)))

    async def of_turns(self, turn_ids: Collection[uuid.UUID]) -> list[RunRecord]:
        if not turn_ids:
            return []
        rows = await self._session.scalars(
            select(RunRecord)
            .where(RunRecord.turn_id.in_(list(turn_ids)))
            .order_by(RunRecord.created_at)
        )
        return list(rows)

    async def last_at(
        self, conversation_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Newest record per conversation."""
        if not conversation_ids:
            return {}
        rows = await self._session.execute(
            select(RunRecord.conversation_id, func.max(RunRecord.created_at))
            .where(RunRecord.conversation_id.in_(list(conversation_ids)))
            .group_by(RunRecord.conversation_id)
        )
        return {cid: at for cid, at in rows.all() if cid is not None and at is not None}

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(RunRecord).where(RunRecord.created_at < cutoff)
        )
        # DELETE returns a CursorResult, which has rowcount at runtime.
        return result.rowcount or 0  # type: ignore[attr-defined]
