"""custom roles table (spec §8.2 自定义专家角色)

One row per custom expert role. Mirrors the Claude Code agents-file shape:
(name, title, description) = frontmatter, body = the persona system prompt.
NULL space_id = a personal role; a custom name shadows a built-in library role.

Revision ID: b7d2e9a41c53
Revises: c3a8f5d19e42
Create Date: 2026-07-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e9a41c53"
down_revision: str | Sequence[str] | None = "c3a8f5d19e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "custom_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        op.f("ix_custom_roles_space_id"), "custom_roles", ["space_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_custom_roles_space_id"), table_name="custom_roles")
    op.drop_table("custom_roles")
