"""What a project's AI teammate prepared to hand in for a member
(`models.PreparedHandIn`): kept until the member agrees, then marked with the
submission it became.

Reading what goes into each item and handing it in are the API's
(`app.api.routes.topics_hand_in`): they read the project's repository and library
and go through the 题目's own submission. This module only keeps the rows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import PreparedHandIn, TaskSubmissionSchemaEntry

#: How a submission item's kind is stored (`task_submission_schema.type`).
_KINDS = {0: "TEXT", 1: "FILE"}


@dataclass(frozen=True)
class FormItem:
    """One item of a 题目's submission form, numbered as the 题目's brief
    lists them (`package.brief`): from 1, across all its stages."""

    number: int
    stage: int
    name: str
    kind: str


async def form_items(session: AsyncSession, task_id: int) -> list[FormItem]:
    """The 题目's submission items, in the form's order."""
    rows = await session.scalars(
        select(TaskSubmissionSchemaEntry)
        .where(TaskSubmissionSchemaEntry.task_id == task_id)
        .order_by(TaskSubmissionSchemaEntry.index)
    )
    return [
        FormItem(
            number=number,
            stage=row.stage,
            name=row.description,
            kind=_KINDS.get(row.type, "TEXT"),
        )
        for number, row in enumerate(rows, start=1)
    ]


async def replace(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    membership_id: int,
    stage: int,
    for_user_id: int,
    for_handle: str,
    prepared_by: str,
    conversation_id: uuid.UUID,
    revision: str,
    items: list[dict],
) -> PreparedHandIn:
    """Keep this preparation for the claim and stage, in place of one not yet
    handed in."""
    await session.execute(
        delete(PreparedHandIn).where(
            PreparedHandIn.membership_id == membership_id,
            PreparedHandIn.stage == stage,
            PreparedHandIn.submission_id.is_(None),
        )
    )
    row = PreparedHandIn(
        project_id=project_id,
        membership_id=membership_id,
        stage=stage,
        for_user_id=for_user_id,
        for_handle=for_handle,
        prepared_by=prepared_by,
        conversation_id=conversation_id,
        revision=revision,
        items=items,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def waiting(
    session: AsyncSession,
    *,
    membership_id: int,
    conversation_id: uuid.UUID,
    stage: int | None,
) -> PreparedHandIn | None:
    """The latest preparation made in this conversation for the claim (in
    ``stage`` when given) that is not handed in yet, held for this request so
    a repeated confirmation cannot hand it in twice."""
    query = (
        select(PreparedHandIn)
        .where(
            PreparedHandIn.membership_id == membership_id,
            PreparedHandIn.conversation_id == conversation_id,
            PreparedHandIn.submission_id.is_(None),
        )
        .order_by(PreparedHandIn.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if stage is not None:
        query = query.where(PreparedHandIn.stage == stage)
    return await session.scalar(query)


def mark_handed_in(row: PreparedHandIn, submission_id: int) -> None:
    """The member agreed and ``row`` became submission ``submission_id``."""
    row.submission_id = submission_id
    row.handed_in_at = datetime.now(UTC)
