from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class PasskeyCredential(Base):
    __tablename__ = "passkey"
    __table_args__ = (Index("uq_passkey_credential_id", "credential_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    counter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    device_type: Mapped[str] = mapped_column(Text, nullable=False)
    backed_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transports: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PasskeyPrompt(Base):
    """How this account has answered the offer to add a passkey after signing in.

    No row means the offer has never been declined. ``snoozed_until`` holds
    the offer back until then; ``ended`` stops it for good, whether the person
    asked for that, declined it too often, or added a passkey.
    """

    __tablename__ = "passkey_prompt"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    dismissals: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
