"""What happened on the default branch after a task's PR merged.

One row per merge: a task that delivers in steps merges once per step. The
platform watches the merge commit for a while (`landing_watch`): the checks the
repository runs on it, and a deployment that includes it. A merge's checks are
a fact about the commit, not about the card that was accepted or the task, so
they live here rather than as more columns on either.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class ChecksOutcome(enum.StrEnum):
    watching = "watching"
    passed = "passed"
    #: A check failed on the merge commit that passed on the commit before it.
    failed = "failed"
    #: Every failing check was failing before the merge too.
    preexisting = "preexisting"
    #: The watch ran out before the checks finished.
    timeout = "timeout"
    #: The forge cannot say (no repository, no credentials).
    unavailable = "unavailable"


class DeployOutcome(enum.StrEnum):
    watching = "watching"
    deployed = "deployed"
    failed = "failed"
    timeout = "timeout"
    #: The repository records no deployments the platform may read.
    unavailable = "unavailable"


class TaskLanding(UuidPk, Timestamps, Base):
    __tablename__ = "task_landings"
    __table_args__ = (
        # The watch reads the ones still being watched, every tick.
        Index("ix_task_landings_watch_until", "watch_until"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "tasks.id", ondelete="CASCADE", name="fk_task_landings_task_id_tasks"
        ),
        index=True,
    )
    pr_number: Mapped[int] = mapped_column(Integer)
    #: The commit the merge put on the default branch; read from the PR the
    #: first time the watch looks, since not every path that sees a merge has it.
    merge_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    landed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    watch_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checks: Mapped[ChecksOutcome] = mapped_column(
        Enum(
            ChecksOutcome,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_task_landings_checks",
        ),
        default=ChecksOutcome.watching,
        server_default=ChecksOutcome.watching.value,
    )
    deploy: Mapped[DeployOutcome] = mapped_column(
        Enum(
            DeployOutcome,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_task_landings_deploy",
        ),
        default=DeployOutcome.watching,
        server_default=DeployOutcome.watching.value,
    )
    #: Where the deployment that included the merge went (GitHub's environment
    #: name, e.g. `dev`).
    environment: Mapped[str | None] = mapped_column(String(255), nullable=True)
