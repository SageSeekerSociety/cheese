from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class UserOAuthConnection(Base):
    __tablename__ = "user_o_auth_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    raw_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
