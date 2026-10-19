"""Give platform-held model connections a team owner.

Revision ID: d4f30c9e71a2
Revises: d1f37c8a46b2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from migration_helpers import with_lock_retries

revision = "d4f30c9e71a2"
down_revision = "d1f37c8a46b2"
branch_labels = None
depends_on = None


def upgrade():
    with_lock_retries("account_model_connections, claude_logins, team")
    op.add_column(
        "account_model_connections",
        sa.Column("team_id", sa.BigInteger(), nullable=True),
    )
    op.alter_column("account_model_connections", "owner_user_id", nullable=True)
    for table in ("account_model_connections", "claude_logins"):
        op.add_column(
            table,
            sa.Column(
                "allowed_models",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
    op.create_foreign_key(
        "fk_account_model_connections_team_id_team",
        "account_model_connections",
        "team",
        ["team_id"],
        ["id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    # Preserve old backend writes until the new backend has been deployed.
    op.execute("""
        CREATE FUNCTION connection_team_during_rollout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.team_id IS NULL THEN
                INSERT INTO team (id, name, intro, description, avatar_id, personal_owner_user_id, created_at, updated_at)
                VALUES (nextval('team_seq'), '个人', '', '', 0, NEW.owner_user_id, now(), now())
                ON CONFLICT (personal_owner_user_id) WHERE personal_owner_user_id IS NOT NULL AND deleted_at IS NULL DO NOTHING;
                SELECT id INTO NEW.team_id FROM team WHERE personal_owner_user_id = NEW.owner_user_id AND deleted_at IS NULL;
                INSERT INTO team_user_relation (id, team_id, user_id, role, created_at, updated_at)
                SELECT nextval('team_user_relation_seq'), NEW.team_id, NEW.owner_user_id, 0, now(), now()
                WHERE NOT EXISTS (SELECT 1 FROM team_user_relation WHERE team_id = NEW.team_id AND user_id = NEW.owner_user_id AND deleted_at IS NULL);
            END IF;
            IF NEW.owner_user_id IS NOT NULL AND NEW.allowed_models = '[]'::jsonb THEN
                NEW.allowed_models := CASE WHEN NEW.model IS NOT NULL THEN jsonb_build_array(NEW.model) ELSE '["opus","sonnet","haiku"]'::jsonb END;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER connection_team_during_rollout BEFORE INSERT ON account_model_connections FOR EACH ROW EXECUTE FUNCTION connection_team_during_rollout()"
    )
    # Old backends can discover a device login during the rolling deployment.
    op.execute("""
        CREATE FUNCTION login_models_during_rollout() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.allowed_models = '[]'::jsonb THEN
                NEW.allowed_models := CASE WHEN NEW.model IS NOT NULL THEN jsonb_build_array(NEW.model) ELSE '["opus","sonnet","haiku"]'::jsonb END;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER login_models_during_rollout BEFORE INSERT ON claude_logins FOR EACH ROW EXECUTE FUNCTION login_models_during_rollout()"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE account_model_connections VALIDATE CONSTRAINT fk_account_model_connections_team_id_team"
        )
    op.execute("""
        INSERT INTO team (id, name, intro, description, avatar_id, personal_owner_user_id, created_at, updated_at)
        SELECT nextval('team_seq'), '个人', '', '', 0, owner_user_id, now(), now()
        FROM (SELECT DISTINCT owner_user_id FROM account_model_connections WHERE owner_user_id IS NOT NULL) owners
        ON CONFLICT (personal_owner_user_id) WHERE personal_owner_user_id IS NOT NULL AND deleted_at IS NULL DO NOTHING
    """)
    op.execute("""
        INSERT INTO team_user_relation (id, team_id, user_id, role, created_at, updated_at)
        SELECT nextval('team_user_relation_seq'), t.id, t.personal_owner_user_id, 0, now(), now()
        FROM team t WHERE t.personal_owner_user_id IN (SELECT owner_user_id FROM account_model_connections)
        AND t.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM team_user_relation r WHERE r.team_id = t.id AND r.user_id = t.personal_owner_user_id AND r.deleted_at IS NULL)
    """)
    op.execute("""
        UPDATE account_model_connections c SET team_id = t.id
        FROM team t WHERE t.personal_owner_user_id = c.owner_user_id
        AND t.deleted_at IS NULL AND c.team_id IS NULL
    """)
    op.execute("""
        UPDATE account_model_connections SET allowed_models = CASE WHEN model IS NOT NULL THEN jsonb_build_array(model) ELSE '["opus","sonnet","haiku"]'::jsonb END WHERE allowed_models = '[]'::jsonb
    """)
    op.execute("""
        UPDATE claude_logins SET allowed_models = CASE WHEN model IS NOT NULL THEN jsonb_build_array(model) ELSE '["opus","sonnet","haiku"]'::jsonb END WHERE allowed_models = '[]'::jsonb
    """)
    op.execute("""
        UPDATE agent_instances a
        SET configuration = a.configuration::jsonb || jsonb_build_object('model', coalesce(c.model, l.model, 'sonnet'))
        FROM own_agents o
        LEFT JOIN account_model_connections c ON c.id = o.account_connection_id
        LEFT JOIN claude_logins l ON l.id = o.login_id
        WHERE a.id = o.instance_id AND a.configuration->>'model' IS NULL
    """)
    op.execute(
        "ALTER TABLE account_model_connections ADD CONSTRAINT ck_account_model_connections_team_present CHECK (team_id IS NOT NULL) NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE account_model_connections VALIDATE CONSTRAINT ck_account_model_connections_team_present"
        )
    with_lock_retries("account_model_connections")
    # migration-safety: allow alter-column-existing — validated CHECK avoids a scan
    op.alter_column(
        "account_model_connections",
        "team_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_constraint(
        "ck_account_model_connections_team_present",
        "account_model_connections",
        type_="check",
    )
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_account_model_connections_team_id"
        )
        op.create_index(
            "ix_account_model_connections_team_id",
            "account_model_connections",
            ["team_id"],
            postgresql_concurrently=True,
        )


def downgrade():
    raise RuntimeError("Team connections cannot be reassigned to an arbitrary user")
