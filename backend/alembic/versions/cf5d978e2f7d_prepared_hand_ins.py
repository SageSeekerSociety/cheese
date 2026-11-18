"""项目的 AI 队友替成员准备的、交到题目的那一份（``prepared_hand_ins``）

队友先准备：每个提交项交什么（仓库里的哪个文件、资料库里的哪一份、或者一段文字），
读到的是哪一版；成员本人之后发消息同意，才照这一份提交。这张表只记准备好的那一份和
它后来成了哪一版提交，题目和提交那几张表不动。

只新建一张表：上一版后端不读它。

辨认 ID: cf5d978e2f7d
Revises: 342495d871e3
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "cf5d978e2f7d"
down_revision: str | Sequence[str] | None = "342495d871e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("projects, task_membership")
    op.create_table(
        "prepared_hand_ins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "projects.id",
                ondelete="CASCADE",
                name="fk_prepared_hand_ins_project_id_projects",
            ),
            nullable=False,
        ),
        sa.Column(
            "membership_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "task_membership.id",
                ondelete="CASCADE",
                name="fk_prepared_hand_ins_membership_id_task_membership",
            ),
            nullable=False,
        ),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("for_user_id", sa.Integer(), nullable=False),
        sa.Column("for_handle", sa.String(64), nullable=False),
        sa.Column("prepared_by", sa.String(128), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=True),
        sa.Column("handed_in_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_prepared_hand_ins_open",
        "prepared_hand_ins",
        ["membership_id", "stage"],
        unique=True,
        postgresql_where=sa.text("submission_id IS NULL"),
    )
    op.create_index(
        "ix_prepared_hand_ins_project_id", "prepared_hand_ins", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_prepared_hand_ins_project_id", table_name="prepared_hand_ins")
    op.drop_index("uq_prepared_hand_ins_open", table_name="prepared_hand_ins")
    op.drop_table("prepared_hand_ins")
