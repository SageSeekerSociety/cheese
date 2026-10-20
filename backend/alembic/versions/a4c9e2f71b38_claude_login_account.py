"""claude logins name their account

Revision ID: a4c9e2f71b38
Revises: e3b7a19c4d52
Create Date: 2026-10-20

A machine can hold several Claude logins, and the team's Models page could not
say which Claude account each one is. Each login keeps the email and
organization `claude auth status` reports for it, refreshed on every check.
Logins checked before this have none until their machine is asked again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "a4c9e2f71b38"
down_revision: str | Sequence[str] | None = "e3b7a19c4d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries("claude_logins")
    op.add_column(
        "claude_logins", sa.Column("account_email", sa.String(320), nullable=True)
    )
    op.add_column(
        "claude_logins", sa.Column("account_org", sa.String(255), nullable=True)
    )


def downgrade() -> None:
    with_lock_retries("claude_logins")
    op.drop_column("claude_logins", "account_org")
    op.drop_column("claude_logins", "account_email")
