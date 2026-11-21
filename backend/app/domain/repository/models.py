"""What the platform keeps of a task's uncommitted work when the project's
repository is not its own (`kept_work`)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class KeptBundle(Base):
    """A task checkout's last snapshot of what it had not committed, as a git
    bundle in the private bucket: one commit whose tree is the checkout's
    files and whose parent is the head they were on (``cut_from``), with only
    the objects the project's repository does not already have. One per task; the next
    snapshot replaces it, and a checkout with nothing uncommitted removes it."""

    __tablename__ = "kept_bundles"
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="CASCADE", name="fk_kept_bundles_task_id_tasks"
        ),
        primary_key=True,
    )
    cut_from: Mapped[str] = mapped_column(String(64))
    commit: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
