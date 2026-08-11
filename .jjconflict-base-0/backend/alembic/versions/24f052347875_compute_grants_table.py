"""compute grants table (spec §9.1 机构提供算力 → real quotas)

One row per credit grant issued to a project when it links a Task whose
Template resource_pack carries {"compute_credits": N}. A project with no
grants is unlimited (spec §4 自治); turn token usage is folded into credits
and deducted oldest grant first.

Revision ID: 24f052347875
Revises: b7d2e9a41c53
Create Date: 2026-07-04 00:02:44.339360

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24f052347875"
down_revision: str | Sequence[str] | None = "b7d2e9a41c53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "compute_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_task_id", sa.Uuid(), nullable=True),
        sa.Column("credits_total", sa.Float(), nullable=False),
        sa.Column("credits_used", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_compute_grants_project_id"),
        "compute_grants",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_compute_grants_project_id"), table_name="compute_grants")
    op.drop_table("compute_grants")
