"""Fence grouped Ask wakes carried by ordinary prompts before native I/O."""

import uuid
from dataclasses import replace

from sqlalchemy import or_, select

from app.core.errors import ValidationError
from app.domain.block.models import Block, conversation_of
from app.domain.delivery.models import Delivery


async def guard_ask_inputs(session, identity, effects):
    ids = (
        set(effects.held_block_ids)
        | set(effects.block_ids)
        | set(effects.seen_block_ids)
    )
    blocks = await session.scalars(select(Block).where(Block.id.in_(ids)))
    events = set()
    for block in blocks:
        meta = block.meta or {}
        if not meta.get("answer_group"):
            continue
        if (
            block.project_id != identity.project_id
            or conversation_of(block) != identity.topic_id
        ):
            raise ValidationError("Ask answer belongs to another room")
        try:
            events.add(uuid.UUID(meta["delivery_event_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Ask answer has no exact delivery event") from exc
    if not events:
        return effects
    # Acquire every delivery before Input and Block locks, including the explicit
    # delivery if this input also carries independent Ask wakes.
    deliveries = list(
        await session.scalars(
            select(Delivery)
            .where(
                or_(
                    Delivery.id == effects.delivery_id,
                    (Delivery.event_id.in_(events))
                    & (Delivery.topic_id == identity.topic_id)
                    & (Delivery.recipient_handle == identity.recipient_handle),
                )
            )
            .order_by(Delivery.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    matched = set()
    members = set()
    for delivery in deliveries:
        if delivery.event_id not in events:
            continue
        origin = delivery.payload.get("ask_origin")
        if not origin or any(
            origin.get(field) != getattr(identity, field)
            for field in ("recipient_handle", "harness", "native_session_id")
        ):
            raise ValidationError(
                "Ask answer cannot enter a replacement native session"
            )
        try:
            members.update(uuid.UUID(value) for value in delivery.payload["block_ids"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Ask delivery has invalid member effects") from exc
        matched.add(delivery.event_id)
    if matched != events:
        raise ValidationError("Ask answer has no delivery for this receiver")
    return replace(
        effects,
        held_block_ids=tuple(sorted(set(effects.held_block_ids) | members)),
        seen_block_ids=tuple(sorted(set(effects.seen_block_ids) | members)),
        seen_by=identity.recipient_handle,
    )
