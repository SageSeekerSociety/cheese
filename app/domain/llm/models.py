from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AIUserQuota(Base):
    __tablename__ = "ai_user_quota"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reset_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
