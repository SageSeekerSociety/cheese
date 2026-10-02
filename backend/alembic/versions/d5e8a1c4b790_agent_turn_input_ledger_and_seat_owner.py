"""the durable input ledger and the per-seat owner/high-water (FB-56)

Revision ID: d5e8a1c4b790
Revises: c4d7e2a9f158
Create Date: 2026-10-01 02:05:00.000000

A platform input (one submission of a prompt to a session) gets a durable
ledger row BEFORE it is sent: a high-entropy nonce planted in the text is
what later binds the native user entry back to this input — no position,
no content guessing, no pre-RPC owner stamp. The per-seat owner record
carries the current owner and the high-water mark used by the
compare-and-set transition; closed owners keep their head so a replayed or
old-generation callback can never roll it back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e8a1c4b790"
down_revision: str | Sequence[str] | None = "c4d7e2a9f158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_turn_inputs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_turns.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # The marker planted in the prompt text. One input, one nonce, for
        # the input's whole life; a retry of the SAME input reuses it, a new
        # input mints a new one. Unique: two live inputs never share one.
        sa.Column("nonce", sa.String(32), nullable=False, unique=True),
        sa.Column("harness", sa.String(16), nullable=False),
        # The native session the input went to, and the journal generation
        # (epoch) it was seen in. Both arrive after send — the session's own
        # info is what reports them — so both are nullable until then.
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("journal_generation", UUID(as_uuid=True), nullable=True),
        # sent -> delivered -> bound; cleared and unknown are terminal
        # dispositions recorded as facts, never as guesses.
        sa.Column("state", sa.String(16), nullable=False, server_default="sent"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        # The binding: which native entry proved this input was consumed, and
        # where in the mirror it sits (the high-water candidate).
        sa.Column("bound_entry_id", sa.String(64), nullable=True),
        sa.Column("bound_pos", sa.BigInteger, nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "agent_seat_owner",
        sa.Column("topic_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_handle", sa.String(64), nullable=False),
        sa.Column("journal_generation", UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        # The work currently owning the seat, and the input that proved it.
        sa.Column("owner_turn_id", UUID(as_uuid=True), nullable=True),
        sa.Column("owner_input_id", UUID(as_uuid=True), nullable=True),
        # The high-water mark of what this seat has processed. Kept even
        # after the owner row behind it closes — it is the compare baseline,
        # not a claim about liveness.
        sa.Column("head_pos", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("head_entry_id", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("topic_id", "agent_handle", "journal_generation"),
    )


def downgrade() -> None:
    op.drop_table("agent_seat_owner")
    op.drop_table("agent_turn_inputs")
