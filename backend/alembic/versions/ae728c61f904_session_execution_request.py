"""Keep a session's execution request separate from its acquired lease."""

import sqlalchemy as sa

from alembic import op

revision = "ae728c61f904"
down_revision = "b672a09ef831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("execution_request", sa.JSON()))
    op.add_column("dispatches", sa.Column("session_id", sa.Uuid()))
    op.add_column("dispatches", sa.Column("lease_generation", sa.Uuid()))
    op.add_column("dispatches", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("dispatches", sa.Column("confirmed_by", sa.String(64)))
    op.add_column("dispatches", sa.Column("confirmation_note", sa.String(2000)))
    op.create_index("ix_dispatches_session_id", "dispatches", ["session_id"])


def downgrade() -> None:
    op.drop_column("dispatches", "confirmation_note")
    op.drop_column("dispatches", "confirmed_by")
    op.drop_column("dispatches", "confirmed_at")
    op.drop_column("agent_sessions", "execution_request")
    op.drop_index("ix_dispatches_session_id", table_name="dispatches")
    op.drop_column("dispatches", "lease_generation")
    op.drop_column("dispatches", "session_id")
