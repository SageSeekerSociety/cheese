"""A project skill as its routes answer with it, and one conversation's
undecided ones.

`_skill` is the row every skill route returns (`GET /projects/{id}/skills`,
`POST`, `PATCH`, `GET /{name}`, 确认, 退回, 恢复), and
`proposals_in_conversation` is the slice of it that belongs to one conversation:
the ones an AI teammate proposed there and nobody has decided on yet. That slice
is what that conversation's proposal card draws — `GET /projects/{id}/skills`
lists the project's, and the `skill_proposals` of the room snapshot a
subscription opens with (`app.api.room_snapshot`) embeds the same rows, so a card
drawn from the snapshot and the same card re-read from the route are one reading.

The two live here, not in `routes/project_skills.py`, because the snapshot is not
a route and must not become one reaching a route (C4 `routes-independent`).
`_iso` and `_skill` stay private and are imported back by that module on purpose:
the rows it returns are built by the same function, and a second copy of the
shape would drift from this one.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project_skill.models import ProjectSkill
from app.domain.project_skill.service import ProjectSkillService


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _skill(row: ProjectSkill) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "name": row.name,
        "title": row.title,
        "description": row.description,
        "body": row.body,
        "files": row.files,
        "state": row.state,
        "origin": row.origin,
        "shipped_revision": row.shipped_revision,
        "proposed_by": row.proposed_by,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": _iso(row.confirmed_at),
        "source_topic_id": str(row.source_topic_id) if row.source_topic_id else None,
        "proposal": row.proposal,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def proposals_in_conversation(
    db: AsyncSession, project_id: uuid.UUID, conversation_id: uuid.UUID
) -> list[dict]:
    """Skills an AI teammate proposed in this conversation and nobody has
    decided on yet, as `GET /projects/{id}/skills` lists them: what that
    conversation's proposal card draws. The `skill_proposals` of the room
    snapshot a subscription opens with; the caller has authorised the reader."""
    conversation = str(conversation_id)
    return [
        item
        for item in (_skill(r) for r in await ProjectSkillService(db).list(project_id))
        if item["state"] == "draft"
        and item["proposal"]
        and item["source_topic_id"] == conversation
    ]
