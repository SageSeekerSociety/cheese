"""topic_memberships for 话题成员名册 (群聊房间的地基)

Revision ID: a1c9f3e70b21
Revises: e15e207f8bef
Create Date: 2026-07-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9f3e70b21"
down_revision: str | Sequence[str] | None = "e15e207f8bef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topic_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("member_handle", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "member_handle", name="uq_topic_member"),
    )
    op.create_index(
        op.f("ix_topic_memberships_topic_id"),
        "topic_memberships",
        ["topic_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_topic_memberships_member_handle"),
        "topic_memberships",
        ["member_handle"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_topic_memberships_member_handle"), table_name="topic_memberships"
    )
    op.drop_index(op.f("ix_topic_memberships_topic_id"), table_name="topic_memberships")
    op.drop_table("topic_memberships")
