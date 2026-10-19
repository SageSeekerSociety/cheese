"""One culprit search over a red batch, kept between the sweeps that run it.

A batch's failure is searched for the commit that introduced it, one CI probe
at a time (`culprit_bisect`). A probe outlives the sweep that dispatched it, so
the plan is stored as it stands: `state` holds the search (`Search` as data),
and `run_id`/`dispatched_at` hold the probe whose result the next sweep reads.

One row per (project, batch head): every task's merge in the batch is watched
by its own landing, and they must all read the same search rather than each
starting one over the same commits.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class CulpritSearch(UuidPk, Timestamps, Base):
    __tablename__ = "culprit_searches"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "head", name="uq_culprit_searches_project_id_head"
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_culprit_searches_project_id_projects",
        ),
        index=True,
    )
    #: The commit the batch's gate reported on: what makes one batch one search.
    head: Mapped[str] = mapped_column(String(64))
    #: The test files each probe reruns, as the red job's log named them.
    tests: Mapped[list] = mapped_column(JSONB, default=list)
    #: The search as `culprit_bisect.Search`: commits, window, probes, verdicts.
    state: Mapped[dict] = mapped_column(JSONB)
    #: The probe run currently out, and when it was dispatched.
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
