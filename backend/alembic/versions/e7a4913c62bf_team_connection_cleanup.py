"""Retire member ownership after the team-owned backend has deployed.

Revision ID: e7a4913c62bf
Revises: d4f30c9e71a2
"""

from migration_helpers import with_lock_retries

from alembic import op

revision = "e7a4913c62bf"
down_revision = "d4f30c9e71a2"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries('account_model_connections, claude_logins, "user"')
    op.execute(
        "DROP TRIGGER connection_team_during_rollout ON account_model_connections"
    )
    op.execute("DROP FUNCTION connection_team_during_rollout()")
    op.execute("DROP TRIGGER login_models_during_rollout ON claude_logins")
    op.execute("DROP FUNCTION login_models_during_rollout()")
    op.drop_column("account_model_connections", "owner_user_id")
    op.execute("""
        UPDATE projects
        SET settings = (settings::jsonb
            - 'allow_own_agents'
            - 'own_agent_kinds'
            - 'own_agent_service_hosts')::json
        WHERE settings::jsonb ?| ARRAY[
            'allow_own_agents', 'own_agent_kinds', 'own_agent_service_hosts'
        ]
    """)


def downgrade():
    raise RuntimeError(
        "Retired member ownership and project allowlists cannot be reconstructed"
    )
