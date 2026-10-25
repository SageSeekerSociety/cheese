"""Record what merging another deployment's export wrote, batch by batch

Revision ID: a7d3e5c91f20
Revises: 168348b68d6c
Create Date: 2026-10-25

``scripts/merge_deployment.py`` brings rows and files from another Cheese
deployment into this one. These three tables are its journal
(``app.core.deployment_merges``): the batch, every row it inserted or updated
(with the columns an update changed, as they were), and every file it created.
The revert of a batch reads them back; nothing else does.

Three new tables, referenced by nothing that exists yet, so nothing the
running release reads is touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7d3e5c91f20"
down_revision: str | Sequence[str] | None = "168348b68d6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_merge_batches",
        sa.Column("batch", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequences", postgresql.JSONB(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("batch"),
    )
    op.create_table(
        "deployment_merge_rows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("target_key", postgresql.JSONB(), nullable=False),
        sa.Column("source_key", postgresql.JSONB(), nullable=True),
        sa.Column("previous", postgresql.JSONB(), nullable=True),
        sa.Column("written", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch"],
            ["deployment_merge_batches.batch"],
            ondelete="CASCADE",
            name="fk_deployment_merge_rows_batch_deployment_merge_batches",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployment_merge_rows_batch", "deployment_merge_rows", ["batch"]
    )
    op.create_table(
        "deployment_merge_files",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch", sa.String(100), nullable=False),
        sa.Column("store", sa.String(20), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch"],
            ["deployment_merge_batches.batch"],
            ondelete="CASCADE",
            name="fk_deployment_merge_files_batch_deployment_merge_batches",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployment_merge_files_batch", "deployment_merge_files", ["batch"]
    )


def downgrade() -> None:
    op.drop_table("deployment_merge_files")
    op.drop_table("deployment_merge_rows")
    op.drop_table("deployment_merge_batches")
