"""A person's 芝士 keeps its conversation in its session, not in the database

Revision ID: b3f8d2e6a174
Revises: a7e3c5d91f20
Create Date: 2026-10-02

A conversation's model history and its summary were what the in-process
assistant sent the model; its session on the session host keeps that now, so
both columns go. What was said stays in ``assistant_messages``, and is what an
older conversation's session is given the first time it is asked something.

The shared key the in-process assistant called the gateway on is retired with
it: every person's 芝士 calls the model on that person's own key.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3f8d2e6a174"
down_revision: str | Sequence[str] | None = "a7e3c5d91f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("assistant_conversations", "history")
    op.drop_column("assistant_conversations", "summary")
    op.execute("DELETE FROM service_credentials WHERE name = 'assistant-gateway-key'")


def downgrade() -> None:
    op.add_column(
        "assistant_conversations",
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "assistant_conversations",
        sa.Column(
            "history",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
