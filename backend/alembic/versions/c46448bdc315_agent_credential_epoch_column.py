"""A project's credential generation is a column of its own

Revision ID: c46448bdc315
Revises: 9f2b7c14a8e3
Create Date: 2026-10-08

The generation lived in `projects.settings`. Revoking read the row, added one and
wrote the whole blob back — and so does every other settings writer, none of them
under a lock. One that read before a revoke landed and flushed after it could put
the old generation back, making every credential that revoke had just retired
work again. A generation has to advance in the database, so it gets a column:
`UPDATE ... SET col = col + 1` is atomic, and a writer that replaces `settings`
wholesale cannot touch it.

What settings already held moves into the column and the key leaves the blob, so
the two can never disagree.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "c46448bdc315"
down_revision: str | Sequence[str] | None = "9f2b7c14a8e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("projects")
    op.add_column(
        "projects",
        sa.Column(
            "agent_credential_epoch", sa.Integer(), server_default="0", nullable=False
        ),
    )
    # The column is the truth from here on. `settings` is json, not jsonb, so
    # reading the key and removing it both go through a cast; both SET
    # expressions read the row as it was, so the copy sees the old blob.
    op.execute(
        "UPDATE projects"
        " SET agent_credential_epoch ="
        " COALESCE((settings::jsonb ->> 'agent_credential_epoch')::int, 0),"
        " settings = (settings::jsonb - 'agent_credential_epoch')::json"
        " WHERE settings::jsonb ? 'agent_credential_epoch'"
    )


def downgrade() -> None:
    # The generation goes back into the blob, then the column goes: a database
    # the previous release reads and writes again. Nothing is lost by stepping
    # back, which is why this one does not raise.
    with_lock_retries("projects")
    op.execute(
        "UPDATE projects"
        " SET settings = (settings::jsonb"
        " || jsonb_build_object('agent_credential_epoch', agent_credential_epoch))::json"
        " WHERE agent_credential_epoch <> 0"
    )
    op.drop_column("projects", "agent_credential_epoch")
