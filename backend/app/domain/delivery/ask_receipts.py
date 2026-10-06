"""Read-only receipts for an exact Ask event and original recipient.

Reading never sends, reconciles or releases. RPC acceptance and native echo
cannot stand in for model-work completion.
"""

import uuid

from sqlalchemy import and_, or_, select

from app.domain.delivery.models import Delivery, NativeInput


async def ask_receipt(session, *, event_id, project_id, topic_id, recipient):
    event_id = uuid.UUID(str(event_id))
    row = await session.scalar(
        select(Delivery).where(
            Delivery.event_id == event_id,
            Delivery.conversation_id == topic_id,
            Delivery.recipient_handle == recipient,
        )
    )
    if row is None:
        return {
            "event_id": str(event_id),
            "state": "unavailable",
            "attempts": 0,
            "last_error": "原执行者不可寻址，尚无投递回执",
            "sent_at": None,
            "received_at": None,
            "completed_at": None,
        }
    inputs = list(
        await session.scalars(
            select(NativeInput).where(
                or_(
                    and_(
                        NativeInput.delivery_id == row.id,
                        NativeInput.event_id == event_id,
                    ),
                    NativeInput.id.in_(
                        [
                            uuid.UUID(value)
                            for value in row.payload.get("consumed_answer_inputs", [])
                        ]
                    ),
                ),
                NativeInput.project_id == project_id,
                NativeInput.conversation_id == topic_id,
                NativeInput.recipient_handle == recipient,
                NativeInput.harness == row.payload["ask_origin"]["harness"],
                NativeInput.native_session_id
                == row.payload["ask_origin"]["native_session_id"],
            )
        )
    )
    settled = [item.settled_at for item in inputs if item.settled_at]
    completed = (
        max(item.completed_at for item in inputs)
        if inputs and all(item.completed_at for item in inputs)
        else None
    )
    return {
        "event_id": str(event_id),
        "state": row.state,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "received_at": max(settled).isoformat() if settled else None,
        "completed_at": completed.isoformat() if completed else None,
    }
