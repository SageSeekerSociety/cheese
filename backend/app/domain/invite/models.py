from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class InviteCode(Base):
    __tablename__ = "invite_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The person whose own code this is: whoever registers with it was invited
    #: by them. None on a code an administrator issued, which invites nobody's
    #: friend and earns nobody a reward.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user.id", ondelete="CASCADE", name="fk_invite_code_owner_user_id_user"
        ),
        nullable=True,
        unique=True,
    )
    #: When the owner's own first accepted delivery added the extra uses.
    unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InviteReferral(Base):
    """Who brought whom: written once, when the invitee registers with
    someone's own code, and never moved to another inviter afterwards."""

    __tablename__ = "invite_referrals"

    invitee_user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id",
            ondelete="CASCADE",
            name="fk_invite_referrals_invitee_user_id_user",
        ),
        primary_key=True,
    )
    inviter_user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user.id",
            ondelete="CASCADE",
            name="fk_invite_referrals_inviter_user_id_user",
        ),
        index=True,
    )
    code_id: Mapped[int] = mapped_column(
        ForeignKey(
            "invite_code.id",
            ondelete="CASCADE",
            name="fk_invite_referrals_code_id_invite_code",
        ),
        index=True,
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    #: The invitee finished the new-user guide and has a project.
    onboarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The invitee accepted their first delivery inside the activation window.
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
