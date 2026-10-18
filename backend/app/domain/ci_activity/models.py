"""The CI activity tables: workflow runs, their jobs, the jobs' steps, the merge
queue's events, and the sweeps that filled them.

Five tables because these are five kinds of thing, each read on its own: the
page groups jobs by runner without touching steps, reads steps for one job
name at a time, and reads queue events without touching Actions at all. A job's
steps are a table and not a JSON list on the job because the page asks for
them by name across thousands of jobs.

Every timestamp named after a GitHub field is GitHub's value for that field,
not when this row was written; ``ingested_at`` is ours.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class CiWorkflowRun(UuidPk, Base):
    """One GitHub Actions workflow run, as the run listing last described it.

    A re-run keeps the run's id and raises ``run_attempt``; the row is updated
    in place. ``jobs_attempt`` is the attempt whose jobs are stored, so a run
    whose jobs are behind its attempt (or were never read) is one the sweep
    still owes a jobs read.
    """

    __tablename__ = "ci_workflow_runs"

    repo: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[int] = mapped_column(BigInteger)
    workflow_id: Mapped[int] = mapped_column(BigInteger)
    workflow_name: Mapped[str] = mapped_column(String(300))
    workflow_path: Mapped[str] = mapped_column(String(300))
    # pull_request | merge_group | push | schedule | workflow_dispatch | ...
    event: Mapped[str] = mapped_column(String(40))
    head_branch: Mapped[str | None] = mapped_column(String(300), nullable=True)
    head_sha: Mapped[str] = mapped_column(String(64))
    # The pull request this run tested: from the run's own `pull_requests` on a
    # pull_request run, from the queue branch's name on a merge_group run.
    # NULL for anything else, and for a fork's PR (GitHub lists none there).
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    conclusion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    jobs_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("repo", "run_id", name="uq_ci_workflow_runs_repo_run_id"),
        Index("ix_ci_workflow_runs_repo_created_at", "repo", "created_at"),
        Index("ix_ci_workflow_runs_repo_pr_number", "repo", "pr_number"),
    )


class CiJob(UuidPk, Base):
    """One job of one attempt of a run (`/actions/runs/{id}/jobs?filter=all`).

    ``runner_kind`` is read off the job's runner group: ``hosted`` for
    GitHub's own ("GitHub Actions"), ``self_hosted`` for any other group, NULL
    for a job that never got a runner (skipped, or cancelled while queued).
    ``labels`` are the labels the job asked for (`runs-on`), which is what
    names a hosted runner: GitHub's runner names are one per job.
    """

    __tablename__ = "ci_jobs"

    run_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "ci_workflow_runs.id",
            ondelete="CASCADE",
            name="fk_ci_jobs_run_row_id_ci_workflow_runs",
        )
    )
    repo: Mapped[str] = mapped_column(String(200))
    job_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))
    conclusion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runner_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    runner_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    labels: Mapped[list[str]] = mapped_column(ARRAY(String(200)))

    __table_args__ = (
        UniqueConstraint("repo", "job_id", name="uq_ci_jobs_repo_job_id"),
        Index("ix_ci_jobs_run_row_id", "run_row_id"),
        Index("ix_ci_jobs_repo_started_at", "repo", "started_at"),
    )


class CiJobStep(UuidPk, Base):
    """One step of one job that ran. Skipped steps are not kept: they have no
    time, and a step is only ever read for how long it took."""

    __tablename__ = "ci_job_steps"

    job_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "ci_jobs.id", ondelete="CASCADE", name="fk_ci_job_steps_job_row_id_ci_jobs"
        )
    )
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(300))
    conclusion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "job_row_id", "number", name="uq_ci_job_steps_job_row_id_number"
        ),
    )


class CiMergeQueueEvent(UuidPk, Base):
    """A pull request entering or leaving the merge queue (GraphQL
    ``AddedToMergeQueueEvent`` / ``RemovedFromMergeQueueEvent``).

    ``kind`` is ``added`` or ``removed``. A removal carries GitHub's reason as
    GitHub spells it (``MERGED``, ``FAILED_CHECKS``, ``MERGE_CONFLICT``,
    ``MANUAL``, ...) and who did it; a merge through the queue is the removal
    whose reason is ``MERGED``.
    """

    __tablename__ = "ci_merge_queue_events"

    repo: Mapped[str] = mapped_column(String(200))
    pr_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The PR's head branch when this was read. GitHub lists a pull_request
    # run's PR only while that branch exists; after a merge deletes it, the
    # branch name is what ties the PR's CI runs to it.
    head_branch: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "repo", "pr_number", "kind", "at", name="uq_ci_merge_queue_events_event"
        ),
        Index("ix_ci_merge_queue_events_repo_at", "repo", "at"),
    )


class CiActivitySweep(UuidPk, Base):
    """One pass of the sweep: what it read, where its two cursors stand, and
    GitHub's words when it could not finish.

    The run listing has covered run-creation times ``[runs_listed_from,
    runs_listed_to)`` without a gap: the backfill walks the first back to the
    window's start, and the next sweep lists from the second (or earlier) so a
    deployment that was down for a day leaves no hole. ``queue_read_to`` is the
    newest PR-update time the queue read has covered. Each sweep starts from
    the previous sweep's values.
    """

    __tablename__ = "ci_activity_sweeps"

    repo: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    runs_listed_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runs_listed_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    queue_read_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_ci_activity_sweeps_repo_started_at", "repo", "started_at"),
    )
