"""drop the external-problem tables

Revision ID: 6ac10b7f1b11
Revises: c0ab1abfc178

#3658 took the YOJ binding out of the platform (no judge-specific field, no
stored outside-site credential, no per-site sync), and that release stopped
mapping the three tables `c4e1a7d90b25` created. This is the second half of
the two-release drop: the tables and their sequence go, and with them any
sealed account password an admin saved for the outside site.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6ac10b7f1b11"
down_revision: str | Sequence[str] | None = "c0ab1abfc178"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("external_problem_result")
    op.drop_table("task_external_problem")
    op.execute(sa.schema.DropSequence(sa.Sequence("task_external_problem_seq")))
    op.drop_table("external_problem_account")


def downgrade() -> None:
    # Gives back the tables' shape only: the rows are gone, and empty tables
    # are what a downgrade can honestly restore. It exists because migration
    # tests downgrade through head to reach older revisions.
    op.create_table(
        "external_problem_account",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", name="uq_external_problem_account_source"),
    )

    # The column below takes its default from this sequence, so it has to exist
    # first — `sa.Sequence` on a column does not create anything by itself.
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_external_problem_seq")))

    op.create_table(
        "task_external_problem",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_external_problem_seq"),
            server_default=sa.text("nextval('task_external_problem_seq')"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("problem_no", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_status",
            sa.String(length=16),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "last_error", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "source", name="uq_task_external_problem_task_source"
        ),
    )

    op.create_table(
        "external_problem_result",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("student_no", sa.String(length=64), nullable=False),
        sa.Column("solved", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "member_id", "source"),
    )
