import uuid

from sqlalchemy import Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class VoiceInput(UuidPk, Timestamps, Base):
    """One recording a person turned into text at the composer.

    What it cost lives in ``resource_usage`` with the project's other model
    calls, which are not split by person; this row is the person's side: how
    many seconds they spoke, which the daily allowance is counted from.
    """

    __tablename__ = "voice_inputs"
    __table_args__ = (
        # What the allowance reads: one person's recordings since a moment.
        Index("ix_voice_inputs_user_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE", name="fk_voice_inputs_user_id_user")
    )
    # The project it was said in; empty when it was said to the person's own
    # 芝士, which is in none.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_voice_inputs_project_id_projects",
        ),
        nullable=True,
        index=True,
    )
    # The room, task or 支线 it was said in.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "conversations.id",
            ondelete="SET NULL",
            name="fk_voice_inputs_conversation_id_conversations",
        ),
        nullable=True,
        index=True,
    )
    seconds: Mapped[float] = mapped_column(Float)
