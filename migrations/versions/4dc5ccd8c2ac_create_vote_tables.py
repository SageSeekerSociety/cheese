"""create_vote_tables

Revision ID: 4dc5ccd8c2ac
Revises: 1c64f1712118
Create Date: 2025-12-02 23:18:12.171223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4dc5ccd8c2ac'
down_revision: Union[str, Sequence[str], None] = '1c64f1712118'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "question_vote",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("question.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vote_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("question_id", "user_id", name="uq_question_vote"),
    )
    op.create_index("ix_question_vote_question_id", "question_vote", ["question_id"])
    op.create_index("ix_question_vote_user_id", "question_vote", ["user_id"])

    op.create_table(
        "answer_vote",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("answer_id", sa.Integer(), sa.ForeignKey("answer.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vote_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("answer_id", "user_id", name="uq_answer_vote"),
    )
    op.create_index("ix_answer_vote_answer_id", "answer_vote", ["answer_id"])
    op.create_index("ix_answer_vote_user_id", "answer_vote", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_answer_vote_user_id", table_name="answer_vote")
    op.drop_index("ix_answer_vote_answer_id", table_name="answer_vote")
    op.drop_table("answer_vote")
    op.drop_index("ix_question_vote_user_id", table_name="question_vote")
    op.drop_index("ix_question_vote_question_id", table_name="question_vote")
    op.drop_table("question_vote")
