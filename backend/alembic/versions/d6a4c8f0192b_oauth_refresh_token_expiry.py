"""Store the expiry GitHub returns with a refresh token."""

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision = "d6a4c8f0192b"
down_revision = "b4d7e19a3c58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with_lock_retries("user_o_auth_connection")
    op.add_column(
        "user_o_auth_connection",
        sa.Column("refresh_token_expires", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with_lock_retries("user_o_auth_connection")
    op.drop_column("user_o_auth_connection", "refresh_token_expires")
