"""Add answer_query_log table.

Mirrors NestJS's AnswerQueryLog (prisma schema.prisma). Used to track
answer views so the frontend's answer.view_count renders real data
instead of placeholder zeros.

Revision ID: 5a3b7c9d1e2f
Revises: 4f2c8e1a9b3d
Create Date: 2026-05-09
"""

import sqlalchemy as sa
from alembic import op

revision = "5a3b7c9d1e2f"
down_revision = "4f2c8e1a9b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_query_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("viewer_id", sa.Integer(), nullable=True),
        sa.Column("answer_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answer_query_log_answer_id", "answer_query_log", ["answer_id"])
    op.create_index("ix_answer_query_log_viewer_id", "answer_query_log", ["viewer_id"])


def downgrade() -> None:
    op.drop_index("ix_answer_query_log_viewer_id", table_name="answer_query_log")
    op.drop_index("ix_answer_query_log_answer_id", table_name="answer_query_log")
    op.drop_table("answer_query_log")
