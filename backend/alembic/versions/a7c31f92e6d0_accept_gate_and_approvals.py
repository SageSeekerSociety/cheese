"""accept gate + N-approval branch protection (spec §4.4/§9, eval C2)

Machine quality gate: accept_cards grows gate_passed_at / gate_output (the
check's timestamp + output tail; new statuses pending_gate / gate_failed need
no schema change — the status column is a non-native enum string).

主分支保护: new accept_approvals table, one row per (card, approver) vote;
accept only merges once a project's approvals_required is met.

Revision ID: a7c31f92e6d0
Revises: c3a8f5d19e42
Create Date: 2026-07-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c31f92e6d0"
down_revision: Union[str, Sequence[str], None] = "c3a8f5d19e42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "accept_cards",
        sa.Column("gate_passed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accept_cards",
        sa.Column("gate_output", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "accept_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("approver_handle", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["accept_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_id", "approver_handle", name="uq_accept_approval"),
    )
    op.create_index(
        op.f("ix_accept_approvals_card_id"),
        "accept_approvals",
        ["card_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_accept_approvals_card_id"), table_name="accept_approvals")
    op.drop_table("accept_approvals")
    op.drop_column("accept_cards", "gate_output")
    op.drop_column("accept_cards", "gate_passed_at")
