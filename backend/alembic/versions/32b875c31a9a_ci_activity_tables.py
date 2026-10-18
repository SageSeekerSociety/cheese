"""ci activity tables

Revision ID: 32b875c31a9a
Revises: 569e06806d03

The CI activity page's data (`app.domain.ci_activity`): the platform's own
repository's Actions runs, their jobs, the jobs' steps, merge-queue events, and
merged pull requests, and the sweeps that copy them out of GitHub. New tables, nothing existing
touched, so no lock on a table the live backend uses.

The unique constraints are the sweep's idempotency: runs are upserted on
``(repo, run_id)``, jobs are unique per ``(repo, job_id)``, and a queue event
read twice inserts nothing the second time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision: str = "32b875c31a9a"
down_revision: str | Sequence[str] | None = "569e06806d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ci_workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_name", sa.String(300), nullable=False),
        sa.Column("workflow_path", sa.String(300), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("head_branch", sa.String(300), nullable=True),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("conclusion", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("html_url", sa.String(500), nullable=True),
        sa.Column("jobs_attempt", sa.Integer(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repo", "run_id", name="uq_ci_workflow_runs_repo_run_id"),
    )
    op.create_index(
        "ix_ci_workflow_runs_repo_created_at",
        "ci_workflow_runs",
        ["repo", "created_at"],
    )
    op.create_index(
        "ix_ci_workflow_runs_repo_pr_number",
        "ci_workflow_runs",
        ["repo", "pr_number"],
    )

    op.create_table(
        "ci_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_row_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ci_workflow_runs.id",
                ondelete="CASCADE",
                name="fk_ci_jobs_run_row_id_ci_workflow_runs",
            ),
            nullable=False,
        ),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("conclusion", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_name", sa.String(200), nullable=True),
        sa.Column("runner_kind", sa.String(20), nullable=True),
        sa.Column("labels", ARRAY(sa.String(200)), nullable=False),
        sa.UniqueConstraint("repo", "job_id", name="uq_ci_jobs_repo_job_id"),
    )
    op.create_index("ix_ci_jobs_run_row_id", "ci_jobs", ["run_row_id"])
    op.create_index("ix_ci_jobs_repo_started_at", "ci_jobs", ["repo", "started_at"])

    op.create_table(
        "ci_job_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_row_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ci_jobs.id",
                ondelete="CASCADE",
                name="fk_ci_job_steps_job_row_id_ci_jobs",
            ),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("conclusion", sa.String(20), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "job_row_id", "number", name="uq_ci_job_steps_job_row_id_number"
        ),
    )

    op.create_table(
        "ci_merge_queue_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(60), nullable=True),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("head_branch", sa.String(300), nullable=True),
        sa.UniqueConstraint(
            "repo", "pr_number", "kind", "at", name="uq_ci_merge_queue_events_event"
        ),
    )
    op.create_index(
        "ix_ci_merge_queue_events_repo_at", "ci_merge_queue_events", ["repo", "at"]
    )

    op.create_table(
        "ci_merged_pull_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_branch", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repo", "pr_number", name="uq_ci_merged_pull_requests_repo_pr_number"
        ),
    )
    op.create_index(
        "ix_ci_merged_pull_requests_repo_merged_at",
        "ci_merged_pull_requests",
        ["repo", "merged_at"],
    )

    op.create_table(
        "ci_activity_sweeps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("runs_listed_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runs_listed_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("queue_read_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_ci_activity_sweeps_repo_started_at",
        "ci_activity_sweeps",
        ["repo", "started_at"],
    )


def downgrade() -> None:
    op.drop_table("ci_activity_sweeps")
    op.drop_table("ci_merged_pull_requests")
    op.drop_table("ci_merge_queue_events")
    op.drop_table("ci_job_steps")
    op.drop_table("ci_jobs")
    op.drop_table("ci_workflow_runs")
