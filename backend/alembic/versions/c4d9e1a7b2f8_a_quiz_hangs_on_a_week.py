"""a quiz hangs on a week

A 小测 belongs to one 教学单元: the unit's ``published_at`` is the quiz's
visibility, so there is deliberately no published column here — one rule, one
place. Questions carry their own answer key (never sent to a student); an
attempt is one row per (quiz, user); an answer's ``awarded_points`` NULL is the
review queue for anything a machine cannot judge.

Revision ID: c4d9e1a7b2f8
Revises: 93ac2aa4b5fd
Create Date: 2026-09-22 14:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4d9e1a7b2f8"
down_revision: str | Sequence[str] | None = "93ac2aa4b5fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    for name in (
        "quiz_seq",
        "quiz_question_seq",
        "quiz_attempt_seq",
        "quiz_answer_seq",
    ):
        op.execute(sa.schema.CreateSequence(sa.Sequence(name)))

    op.create_table(
        "quiz",
        sa.Column("id", sa.BigInteger(), sa.Sequence("quiz_seq"), nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("unit_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # 「这节课有没有小测」与「这个单元的小测」—— 唯一的读法，一个单元最多一次。
    op.create_index("ix_quiz_unit", "quiz", ["unit_id"])
    op.create_index("ix_quiz_space", "quiz", ["space_id"])

    op.create_table(
        "quiz_question",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("quiz_question_seq"), nullable=False
        ),
        sa.Column("quiz_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_question_quiz", "quiz_question", ["quiz_id"])

    op.create_table(
        "quiz_attempt",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("quiz_attempt_seq"), nullable=False
        ),
        sa.Column("quiz_id", sa.BigInteger(), nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # 一个人一份：重交是替换，不是追加（见 QuizAttempt 的说明）。
    op.create_index(
        "ix_quiz_attempt_quiz_user", "quiz_attempt", ["quiz_id", "user_id"], unique=True
    )

    op.create_table(
        "quiz_answer",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("quiz_answer_seq"), nullable=False
        ),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column("awarded_points", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("graded_by", sa.Integer(), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_answer_attempt", "quiz_answer", ["attempt_id"])
    op.create_index("ix_quiz_answer_question", "quiz_answer", ["question_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_quiz_answer_question", table_name="quiz_answer")
    op.drop_index("ix_quiz_answer_attempt", table_name="quiz_answer")
    op.drop_table("quiz_answer")
    op.drop_index("ix_quiz_attempt_quiz_user", table_name="quiz_attempt")
    op.drop_table("quiz_attempt")
    op.drop_index("ix_quiz_question_quiz", table_name="quiz_question")
    op.drop_table("quiz_question")
    op.drop_index("ix_quiz_space", table_name="quiz")
    op.drop_index("ix_quiz_unit", table_name="quiz")
    op.drop_table("quiz")
    for name in (
        "quiz_answer_seq",
        "quiz_attempt_seq",
        "quiz_question_seq",
        "quiz_seq",
    ):
        op.execute(sa.schema.DropSequence(sa.Sequence(name)))
