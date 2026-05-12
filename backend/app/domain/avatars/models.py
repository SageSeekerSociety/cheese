from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Avatar(Base):
    __tablename__ = "avatar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    avatar_type: Mapped[str] = mapped_column(
        PgEnum("default", "predefined", "upload", name="AvatarType", create_type=False),
        nullable=False,
    )
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
