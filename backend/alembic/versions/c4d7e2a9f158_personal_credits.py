"""Personal credits: a grant can belong to a person for a month, and spend
outside a project is recorded against that person

Revision ID: c4d7e2a9f158
Revises: 3e8c5a1f7d24
Create Date: 2026-10-01 12:00:00

问芝士 now charges the asker's personal credits instead of a platform-wide
budget on its gateway key. The stored key was minted with that budget, so it
is dropped here and the next question mints one without it.

``resource_usage`` takes an insert on every model call, so nothing here holds
a lock on it for longer than a catalog change: the foreign key is added
``NOT VALID`` and validated afterwards (which does not block writes), and the
index is built ``CONCURRENTLY``, outside the migration's transaction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d7e2a9f158"
down_revision: str | Sequence[str] | None = "3e8c5a1f7d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "compute_grants",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("compute_grants", sa.Column("month", sa.Date(), nullable=True))
    op.create_index(
        "uq_compute_grants_user_month",
        "compute_grants",
        ["user_id", "month"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.alter_column("resource_usage", "project_id", nullable=True)
    op.add_column("resource_usage", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        "ALTER TABLE resource_usage ADD CONSTRAINT resource_usage_user_id_fkey "
        'FOREIGN KEY (user_id) REFERENCES "user" (id) ON DELETE CASCADE NOT VALID'
    )

    op.execute(
        "DELETE FROM service_credentials WHERE name = 'docs-assistant-gateway-key'"
    )

    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE resource_usage VALIDATE CONSTRAINT resource_usage_user_id_fkey"
        )
        op.create_index(
            "ix_resource_usage_user_created_at",
            "resource_usage",
            ["user_id", "created_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_resource_usage_user_created_at",
            table_name="resource_usage",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("resource_usage", "user_id")
    op.execute("DELETE FROM resource_usage WHERE project_id IS NULL")
    op.alter_column("resource_usage", "project_id", nullable=False)

    op.drop_index("uq_compute_grants_user_month", table_name="compute_grants")
    op.execute("DELETE FROM compute_grants WHERE user_id IS NOT NULL")
    op.drop_column("compute_grants", "month")
    op.drop_column("compute_grants", "user_id")
