"""Remove rollout derivation after independent credential ownership deploys.

Revision ID: b8c24e91a7d0
Revises: b6e1d47c2a95
"""

from migration_helpers import with_lock_retries

from alembic import op

revision = "b8c24e91a7d0"
down_revision = "b6e1d47c2a95"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries("claude_logins, account_model_connections")
    op.execute("DROP TRIGGER credential_login_rollout ON claude_logins")
    op.execute("DROP FUNCTION credential_login_rollout()")
    op.execute("DROP TRIGGER credential_account_rollout ON account_model_connections")
    op.execute("DROP FUNCTION credential_account_rollout()")
    op.drop_column("claude_logins", "model")
    op.drop_column("claude_logins", "service_host")


def downgrade():
    raise RuntimeError("Retired computer-held service metadata cannot be reconstructed")
