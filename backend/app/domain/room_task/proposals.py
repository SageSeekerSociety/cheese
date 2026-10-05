"""A task an AI teammate proposes in a room.

A teammate never creates a task: it proposes one, and a person in the room
creates it (and owns it) or puts it aside. The proposal is a thing of its own —
it is proposed, then decided once — so it has its own table rather than riding
on a message.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ProposalState(enum.StrEnum):
    open = "open"
    accepted = "accepted"
    dismissed = "dismissed"


class TaskProposal(UuidPk, Timestamps, Base):
    __tablename__ = "task_proposals"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    #: What the task is for, in the teammate's words: what its document starts
    #: from once the task is created.
    summary: Mapped[str] = mapped_column(Text, default="")
    proposed_by: Mapped[str] = mapped_column(String(64))
    state: Mapped[ProposalState] = mapped_column(
        Enum(ProposalState, name="task_proposal_state", native_enum=False, length=16),
        default=ProposalState.open,
    )
    #: The task created from it, once someone did.
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskProposals:
    """Proposing, listing and deciding proposals."""

    def __init__(self, session) -> None:
        self._session = session

    async def propose(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        title: str,
        summary: str,
        proposed_by: str,
    ) -> TaskProposal:
        proposal = TaskProposal(
            project_id=project_id,
            room_id=room_id,
            title=title,
            summary=summary,
            proposed_by=proposed_by,
            state=ProposalState.open,
        )
        self._session.add(proposal)
        await self._session.flush()
        return proposal

    async def open_in_room(self, room_id: uuid.UUID) -> list[TaskProposal]:
        """The proposals in a room still waiting for someone, oldest first."""
        rows = await self._session.scalars(
            select(TaskProposal)
            .where(
                TaskProposal.room_id == room_id,
                TaskProposal.state == ProposalState.open,
            )
            .order_by(TaskProposal.created_at, TaskProposal.id)
        )
        return list(rows)

    async def lock(self, room_id: uuid.UUID, proposal_id: uuid.UUID):
        """The proposal, locked for deciding; None when the room has no such
        proposal."""
        proposal = await self._session.get(
            TaskProposal, proposal_id, with_for_update=True
        )
        if proposal is None or proposal.room_id != room_id:
            return None
        return proposal

    @staticmethod
    def decide(
        proposal: TaskProposal,
        state: ProposalState,
        *,
        by: str,
        task_id: uuid.UUID | None = None,
    ) -> None:
        proposal.state = state
        proposal.decided_by = by
        proposal.decided_at = datetime.now(UTC)
        proposal.task_id = task_id
