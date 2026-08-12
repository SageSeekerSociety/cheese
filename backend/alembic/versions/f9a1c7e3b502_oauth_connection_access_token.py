"""oauth connection access_token column (#192 account-link token persistence)

The user-to-server GitHub App token exchanged in the account-link callback
was previously discarded — only the identity link was stored. Adds the
missing access_token column so the token itself can be persisted
(encrypted, see app.core.crypto) and read back for later use (#189-style
PR authoring on the user's behalf).

Also merges the two alembic heads left behind by e1f2a3b4c5d6 (webhook
tokens table) and e3a94c6f5b18 (project_git_installations) landing on main
independently — `alembic upgrade head` is ambiguous with two heads, which
was silently breaking the test harness's migration step for any change
made after both merged.

Revision ID: f9a1c7e3b502
Revises: e1f2a3b4c5d6, e3a94c6f5b18
Create Date: 2026-08-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f9a1c7e3b502"
down_revision: str | Sequence[str] | None = ("e1f2a3b4c5d6", "e3a94c6f5b18")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_o_auth_connection",
        sa.Column("access_token", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_o_auth_connection", "access_token")
