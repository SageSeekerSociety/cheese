"""结论卡 (conclusion_cards) — 回流有回执、有状态、有幂等

Revision ID: c8b1f4a70d29
Revises: b8e1d4c70a92
Create Date: 2026-08-11 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8b1f4a70d29"
# Rechained d4a1b6f27c90 → b8e1d4c70a92: main had already landed e7f3a90c15d2 on
# d4a1b6f27c90 while this branch sat on a 34-commit-old base, so keeping the old
# parent forked the chain into two heads.
down_revision: str | Sequence[str] | None = "b8e1d4c70a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Its OWN table, not a row in accept_cards: the receiver is a TOPIC
    # (receiver_topic_id), and accept_cards is swept by status alone by the PR
    # poller — a conclusion card living there would be picked up by machinery
    # that knows nothing about it.
    op.create_table(
        "conclusion_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("receiver_topic_id", sa.Uuid(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "accepted",
                "returned",
                "escalated",
                "superseded",
                name="conclusionstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("settled_by", sa.Text(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settle_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("blocking_ref", sa.Text(), nullable=True),
        sa.Column("returned_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("digest_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["receiver_topic_id"], ["topics.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conclusion_cards_project_id"),
        "conclusion_cards",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conclusion_cards_topic_id"),
        "conclusion_cards",
        ["topic_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conclusion_cards_receiver_topic_id"),
        "conclusion_cards",
        ["receiver_topic_id"],
        unique=False,
    )
    # The two hot lookups: this sub-topic's live card, and the cards a parent
    # still owes a verdict on (the turn-end sweep runs on every turn).
    op.create_index(
        "ix_conclusion_cards_topic_status",
        "conclusion_cards",
        ["topic_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_conclusion_cards_receiver_status",
        "conclusion_cards",
        ["receiver_topic_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_conclusion_cards_receiver_status", table_name="conclusion_cards")
    op.drop_index("ix_conclusion_cards_topic_status", table_name="conclusion_cards")
    op.drop_index(
        op.f("ix_conclusion_cards_receiver_topic_id"), table_name="conclusion_cards"
    )
    op.drop_index(op.f("ix_conclusion_cards_topic_id"), table_name="conclusion_cards")
    op.drop_index(op.f("ix_conclusion_cards_project_id"), table_name="conclusion_cards")
    op.drop_table("conclusion_cards")
