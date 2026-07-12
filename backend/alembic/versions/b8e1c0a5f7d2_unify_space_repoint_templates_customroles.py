"""fusion unify P1c: repoint task_templates + custom_roles to main Space (int), drop cheesex spaces stub  # noqa: E501

Revision ID: b8e1c0a5f7d2
Revises: 4006c4e8b583
Create Date: 2026-07-11 21:00:00.000000

The cheesex `spaces` table (uuid, 3-col stub) duplicated main-cheese's `space`
(int, the real 机构). We retire it: the two FKs that pointed at it
(`task_templates.space_id`, `custom_roles.space_id`) now reference `space.id`
(int). Both tables are empty at merge time, so the uuid→int column swap needs no
data conversion.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8e1c0a5f7d2"
down_revision: str | Sequence[str] | None = "4006c4e8b583"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # task_templates.space_id: uuid → int (dropping the column cascades its FK +
    # index in PostgreSQL; the table is empty so NOT NULL re-add is safe).
    op.drop_column("task_templates", "space_id")
    op.add_column("task_templates", sa.Column("space_id", sa.Integer(), nullable=False))
    op.create_index("ix_task_templates_space_id", "task_templates", ["space_id"])
    op.create_foreign_key(
        "fk_task_templates_space_id",
        "task_templates",
        "space",
        ["space_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # custom_roles.space_id: uuid → int (nullable; NULL = personal role).
    op.drop_column("custom_roles", "space_id")
    op.add_column("custom_roles", sa.Column("space_id", sa.Integer(), nullable=True))
    op.create_index("ix_custom_roles_space_id", "custom_roles", ["space_id"])
    op.create_foreign_key(
        "fk_custom_roles_space_id",
        "custom_roles",
        "space",
        ["space_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Retire the cheesex spaces (uuid) stub — no referrers remain.
    op.drop_table("spaces")


def downgrade() -> None:
    # Recreate the cheesex spaces (uuid) stub.
    op.create_table(
        "spaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.drop_constraint("fk_custom_roles_space_id", "custom_roles", type_="foreignkey")
    op.drop_index("ix_custom_roles_space_id", table_name="custom_roles")
    op.drop_column("custom_roles", "space_id")
    op.add_column("custom_roles", sa.Column("space_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        None, "custom_roles", "spaces", ["space_id"], ["id"], ondelete="SET NULL"
    )

    op.drop_constraint(
        "fk_task_templates_space_id", "task_templates", type_="foreignkey"
    )
    op.drop_index("ix_task_templates_space_id", table_name="task_templates")
    op.drop_column("task_templates", "space_id")
    op.add_column("task_templates", sa.Column("space_id", sa.Uuid(), nullable=False))
    op.create_foreign_key(
        None, "task_templates", "spaces", ["space_id"], ["id"], ondelete="CASCADE"
    )
