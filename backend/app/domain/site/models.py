"""A project's published site and its immutable releases."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class SiteRelease(UuidPk, Base):
    __tablename__ = "site_releases"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_revision: Mapped[str] = mapped_column(String(64))
    directory: Mapped[str] = mapped_column(String(1024))
    entry_file: Mapped[str] = mapped_column(String(255), default="index.html")
    manifest: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str] = mapped_column(String(64))


class Site(Base):
    __tablename__ = "sites"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    current_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("site_releases.id", ondelete="CASCADE")
    )
