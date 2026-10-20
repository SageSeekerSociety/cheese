"""assistant messages keep their parts

Revision ID: e3b7a19c4d52
Revises: d81a6c3f5e20
Create Date: 2026-10-20

What a person's 芝士 did while answering — the tools it called, what it wrote
into drafts and what it published — is kept with the answer
(`assistant_messages.parts`), so the conversation shows it again after a
reload. Answers kept before this have none.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3b7a19c4d52"
down_revision: str | Sequence[str] | None = "d81a6c3f5e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("assistant_messages")
    op.add_column(
        "assistant_messages",
        sa.Column("parts", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("assistant_messages")
    op.drop_column("assistant_messages", "parts")
