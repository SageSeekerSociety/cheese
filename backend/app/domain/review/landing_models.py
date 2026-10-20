"""A task's merge, landed once.

One row per merge that had a PR: a task that delivers in steps merges once per
step. Every path that sees a merge comes through `task_landing.delivery_landed`,
and the row is what keeps the same merge from landing on its task twice.

Nothing is watched after a merge: the columns the post-merge watch kept
(`merge_sha`, `watch_until`, `checks`, `deploy`, `environment`) are no longer
mapped, and are dropped in the next release (`.claude/rules/migrations.md`).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class TaskLanding(UuidPk, Timestamps, Base):
    __tablename__ = "task_landings"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="CASCADE", name="fk_task_landings_task_id_tasks"
        ),
        index=True,
    )
    pr_number: Mapped[int] = mapped_column(Integer)
    landed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
