"""Reading what the platform recorded about a conversation, apart from it."""

import uuid

from sqlalchemy import select

from app.domain.run_record.models import RunRecord


async def records_of(
    session, conversation_id: uuid.UUID | str, kind: str | None = None
):
    """The conversation's run records, oldest first; `kind` narrows them."""
    stmt = select(RunRecord).where(
        RunRecord.conversation_id == uuid.UUID(str(conversation_id))
    )
    if kind is not None:
        stmt = stmt.where(RunRecord.kind == kind)
    return list(await session.scalars(stmt.order_by(RunRecord.created_at)))


def record_frames(frames: list[dict], kind: str) -> list[dict]:
    """Every `run_record` frame of `kind`, in the order they came."""
    return [
        f["record"]
        for f in frames
        if f["type"] == "run_record"
        and (f["record"]["meta"] or {}).get("event_type") == kind
    ]
